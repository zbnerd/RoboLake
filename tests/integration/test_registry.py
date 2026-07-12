"""PostgreSQL-backed registry behavior."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from robolake.application.idempotency import request_fingerprint
from robolake.application.registry import RegistryService
from robolake.domain.errors import ContentConflictError, IdempotencyConflictError
from robolake.domain.identifiers import DatasetName, RelativePath, Sha256Digest
from robolake.domain.lifecycle import BlobState, FailureCode, UploadSessionState, VersionState
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.infrastructure.store import SqlAlchemyStore
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@pytest.fixture
def registry_store(database_connection: Connection) -> Iterator[SqlAlchemyStore]:
    sessions = sessionmaker(
        bind=database_connection,
        class_=Session,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    yield SqlAlchemyStore(sessions)


@pytest.fixture
def registry(registry_store: SqlAlchemyStore) -> Iterator[RegistryService]:
    yield RegistryService(registry_store)


def test_dataset_creation_replays_same_payload_and_rejects_key_reuse(
    registry: RegistryService,
) -> None:
    first = registry.create_dataset(DatasetName.parse("test/idempotent"), "same-key")
    replay = registry.create_dataset(DatasetName.parse("test/idempotent"), "same-key")

    assert replay == first
    with pytest.raises(IdempotencyConflictError):
        registry.create_dataset(DatasetName.parse("test/different"), "same-key")


def _manifest(path: str, digest: str, size_bytes: int) -> Manifest:
    return Manifest.build(
        [
            ManifestEntry(
                RelativePath.parse(path),
                size_bytes,
                Sha256Digest.parse(digest),
            )
        ]
    )


def test_manifest_identity_replays_version_and_changed_manifest_allocates_next_number(
    registry: RegistryService,
) -> None:
    dataset = registry.create_dataset(DatasetName.parse("test/versions"), "dataset-key")
    first_manifest = _manifest("b.bin", "b" * 64, 2)
    changed_manifest = _manifest("a.bin", "a" * 64, 1)

    first = registry.register_version(dataset.id, first_manifest, "version-key-1")
    same_content = registry.register_version(dataset.id, first_manifest, "version-key-replay")
    changed = registry.register_version(dataset.id, changed_manifest, "version-key-2")

    assert same_content == first
    assert str(first.reference) == "test/versions@v1"
    assert first.state is VersionState.DRAFT
    assert str(changed.reference) == "test/versions@v2"
    assert registry.resolve(first.reference) == first
    assert registry.manifest(first.id).canonical_bytes == first_manifest.canonical_bytes


def test_status_separates_logical_files_from_unique_available_content(
    registry: RegistryService, database_connection: Connection
) -> None:
    dataset = registry.create_dataset(DatasetName.parse("test/status"), "status-dataset")
    digest = Sha256Digest.parse("c" * 64)
    manifest = Manifest.build(
        [
            ManifestEntry(RelativePath.parse("left.bin"), 3, digest),
            ManifestEntry(RelativePath.parse("right.bin"), 3, digest),
        ]
    )
    version = registry.register_version(dataset.id, manifest, "status-version")

    pending = registry.status(version.id)
    assert pending.snapshot.file_count == 2
    assert pending.snapshot.logical_bytes == 6
    assert pending.content.unique_blob_count == 1
    assert pending.content.unique_blob_bytes == 3
    assert pending.snapshot.ready_file_count == 0
    assert pending.content.available_blob_count == 0

    for state in ("UPLOADING", "VERIFYING", "AVAILABLE"):
        database_connection.execute(
            text("UPDATE blobs SET state = :state WHERE sha256 = :sha256"),
            {"state": state, "sha256": digest.value},
        )

    available = registry.status(version.id)
    assert available.snapshot.ready_file_count == 2
    assert available.snapshot.ready_logical_bytes == 6
    assert available.content.available_blob_count == 1
    assert available.content.available_blob_bytes == 3


def test_registered_ordinals_follow_canonical_bytes_and_empty_manifest_has_no_entry(
    registry: RegistryService, registry_store: SqlAlchemyStore
) -> None:
    dataset = registry.create_dataset(DatasetName.parse("test/ordinals"), "ordinal-dataset")
    manifest = Manifest.build(
        [
            ManifestEntry(RelativePath.parse("z.bin"), 1, Sha256Digest.parse("d" * 64)),
            ManifestEntry(RelativePath.parse("ä.bin"), 1, Sha256Digest.parse("e" * 64)),
            ManifestEntry(RelativePath.parse("a.bin"), 1, Sha256Digest.parse("f" * 64)),
        ]
    )
    version = registry.register_version(dataset.id, manifest, "ordinal-version")

    assert (
        tuple(
            registry_store.get_download_entry(version.id, ordinal)
            for ordinal in range(manifest.file_count)
        )
        == manifest.entries
    )
    assert registry_store.get_download_entry(version.id, manifest.file_count) is None

    empty = registry.register_version(dataset.id, Manifest.build([]), "empty-version")
    assert empty.is_empty is True
    assert registry_store.get_download_entry(empty.id, 0) is None


def test_conflicting_blob_size_rolls_back_version_registration(registry: RegistryService) -> None:
    dataset = registry.create_dataset(DatasetName.parse("test/blob-conflict"), "conflict-dataset")
    digest = "1" * 64
    first = registry.register_version(dataset.id, _manifest("first.bin", digest, 1), "conflict-v1")

    with pytest.raises(ContentConflictError):
        registry.register_version(dataset.id, _manifest("conflict.bin", digest, 2), "conflict-bad")

    next_version = registry.register_version(
        dataset.id, _manifest("next.bin", "2" * 64, 2), "conflict-v2"
    )
    assert first.version_number == 1
    assert next_version.version_number == 2


def test_concurrent_version_registration_converges_and_allocates_unique_numbers(
    migrated_database_url: str,
) -> None:
    engine = create_engine(migrated_database_url)
    sessions = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    registry = RegistryService(SqlAlchemyStore(sessions))
    suffix = uuid4().hex
    dataset = registry.create_dataset(
        DatasetName.parse(f"test/concurrent-{suffix}"), f"dataset-{suffix}"
    )
    same = _manifest("same.bin", "3" * 64, 3)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(registry.register_version, dataset.id, same, f"same-{suffix}-{index}")
            for index in range(2)
        ]
        same_results = [future.result() for future in futures]

    assert {result.id for result in same_results} == {same_results[0].id}
    assert {result.version_number for result in same_results} == {1}

    left = _manifest("left.bin", "4" * 64, 4)
    right = _manifest("right.bin", "5" * 64, 5)
    with ThreadPoolExecutor(max_workers=2) as executor:
        left_future = executor.submit(registry.register_version, dataset.id, left, f"left-{suffix}")
        right_future = executor.submit(
            registry.register_version, dataset.id, right, f"right-{suffix}"
        )
        changed_results = [left_future.result(), right_future.result()]

    assert {result.version_number for result in changed_results} == {2, 3}
    engine.dispose()


def test_store_persists_file_level_upload_and_finalizes_only_available_content(
    registry: RegistryService, registry_store: SqlAlchemyStore
) -> None:
    dataset = registry.create_dataset(DatasetName.parse("test/upload-flow"), "upload-dataset")
    manifest = _manifest("blob.bin", "7" * 64, 7)
    version = registry.register_version(dataset.id, manifest, "upload-version")
    digest = manifest.entries[0].sha256
    fingerprint = request_fingerprint([version.id.bytes, digest.raw_bytes])

    prepared = registry_store.prepare_upload(version.id, digest, "upload-session", fingerprint)
    replay = registry_store.prepare_upload(version.id, digest, "upload-session", fingerprint)
    assert replay == prepared
    assert prepared.session is not None
    assert prepared.session.state is UploadSessionState.CREATED
    assert prepared.blob.state is BlobState.PENDING

    started = registry_store.mark_upload_in_progress(prepared.session.id)
    assert started.session is not None
    assert started.session.state is UploadSessionState.IN_PROGRESS
    assert started.blob.state is BlobState.UPLOADING
    assert started.version.state is VersionState.UPLOADING

    verified = registry_store.mark_upload_verified(started.session.id, '"etag"')
    assert verified.session is not None
    assert verified.session.state is UploadSessionState.COMPLETED
    assert verified.blob.state is BlobState.AVAILABLE

    ready = registry_store.finalize_version(version.id)
    assert ready.state is VersionState.READY
    assert registry_store.finalize_version(version.id) == ready

    reused = registry_store.prepare_upload(version.id, digest, "available-reuse", fingerprint)
    assert reused.blob.state is BlobState.AVAILABLE
    assert reused.session is None


def test_failed_publication_restarts_same_blob_and_version_after_manual_cleanup(
    registry: RegistryService, registry_store: SqlAlchemyStore
) -> None:
    dataset = registry.create_dataset(DatasetName.parse("test/recovery"), "recovery-dataset")
    manifest = _manifest("recover.bin", "8" * 64, 8)
    version = registry.register_version(dataset.id, manifest, "recovery-version")
    digest = manifest.entries[0].sha256
    fingerprint = request_fingerprint([version.id.bytes, digest.raw_bytes])
    prepared = registry_store.prepare_upload(version.id, digest, "recovery-first", fingerprint)
    assert prepared.session is not None
    started = registry_store.mark_upload_in_progress(prepared.session.id)
    assert started.session is not None

    failed = registry_store.mark_upload_failed(
        started.session.id,
        FailureCode.STORED_OBJECT_MISMATCH,
        "Stored object does not match its content address.",
    )
    assert failed.version.state is VersionState.FAILED
    assert failed.blob.state is BlobState.FAILED
    assert failed.session is not None
    assert failed.session.state is UploadSessionState.FAILED

    restarted = registry_store.restart_failed_upload(
        version.id,
        failed.blob.id,
        "recovery-second",
        fingerprint,
    )
    assert restarted.version.id == version.id
    assert restarted.version.state is VersionState.UPLOADING
    assert restarted.blob.id == failed.blob.id
    assert restarted.blob.state is BlobState.UPLOADING
    assert restarted.session is not None
    assert restarted.session.id != failed.session.id
    assert restarted.session.state is UploadSessionState.CREATED
