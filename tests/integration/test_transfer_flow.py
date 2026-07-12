"""Real PostgreSQL and pinned-MinIO M1 workflow evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from robolake.application.contracts import (
    DownloadCapability,
    PresignedRequest,
    PutReceipt,
    UploadPreparation,
)
from robolake.application.pull import PullWorkflow
from robolake.application.push import PushWorkflow
from robolake.application.registry import RegistryService
from robolake.application.transfers import TransferService
from robolake.domain.errors import (
    RetryableTransferError,
    StoredObjectMismatchError,
    UnsupportedFileSizeError,
)
from robolake.domain.identifiers import DatasetName, DatasetReference, Sha256Digest, object_key_for
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest
from robolake.domain.records import DatasetRecord, VersionRecord, VersionStatus
from robolake.infrastructure.atomic_tree import AtomicTreeDownloader
from robolake.infrastructure.http_transfer import HttpByteTransfer
from robolake.infrastructure.object_storage import (
    S3ObjectStore,
    create_public_s3_client,
    create_s3_client,
)
from robolake.infrastructure.scanner import FileSystemScanner
from robolake.infrastructure.settings import Settings
from robolake.infrastructure.store import SqlAlchemyStore
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class InProcessControlPlane:
    registry: RegistryService
    transfers: TransferService

    def create_dataset(self, name: DatasetName, idempotency_key: str) -> DatasetRecord:
        return self.registry.create_dataset(name, idempotency_key)

    def register_version(
        self, dataset_id: UUID, manifest: Manifest, idempotency_key: str
    ) -> VersionRecord:
        return self.registry.register_version(dataset_id, manifest, idempotency_key)

    def resolve(self, reference: DatasetReference) -> VersionRecord:
        return self.registry.resolve(reference)

    def status(self, version_id: UUID) -> VersionStatus:
        return self.registry.status(version_id)

    def manifest(self, version_id: UUID) -> Manifest:
        return self.registry.manifest(version_id)

    def prepare_upload(
        self, version_id: UUID, digest: Sha256Digest, idempotency_key: str
    ) -> UploadPreparation:
        return self.transfers.prepare_upload(version_id, digest, idempotency_key)

    def upload_url(self, session_id: UUID) -> PresignedRequest:
        return self.transfers.issue_upload_url(session_id)

    def complete_upload(self, session_id: UUID, etag: str | None) -> UploadPreparation:
        return self.transfers.complete_upload(session_id, etag)

    def finalize(self, version_id: UUID) -> VersionRecord:
        return self.transfers.finalize(version_id)

    def download_plan(self, version_id: UUID, cursor: str | None) -> DownloadCapability:
        return self.transfers.download_plan(version_id, cursor)


@dataclass
class FlowServices:
    control: InProcessControlPlane
    scanner: FileSystemScanner
    bytes: HttpByteTransfer
    objects: S3ObjectStore
    database: Connection


@pytest.fixture
def flow_services(database_connection: Connection) -> FlowServices:
    settings = Settings()
    sessions = sessionmaker(
        bind=database_connection,
        class_=Session,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    store = SqlAlchemyStore(sessions)
    objects = S3ObjectStore(
        create_s3_client(settings),
        create_public_s3_client(settings),
        settings.s3_bucket,
    )
    control = InProcessControlPlane(
        RegistryService(store),
        TransferService(
            store,
            objects,
            presigned_url_ttl_seconds=60,
            stream_chunk_bytes=2,
        ),
    )
    transfer = HttpByteTransfer(httpx.Client(timeout=10), chunk_size=2)
    return FlowServices(
        control, FileSystemScanner(chunk_size=2), transfer, objects, database_connection
    )


def _push(flow: FlowServices, source: Path, name: str, transfer: object | None = None):
    return PushWorkflow(
        flow.scanner,
        flow.control,
        transfer or flow.bytes,
        lambda _: None,
    ).run(source, DatasetName.parse(name))


def _inventory(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_push_pull_is_byte_identical_idempotent_and_deduplicated(
    flow_services: FlowServices, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    shared = f"shared-{uuid4()}".encode()
    (source / "a.bin").write_bytes(shared)
    (source / "nested" / "copy.bin").write_bytes(shared)
    (source / "nested" / "unique.bin").write_bytes(b"unique-synthetic")
    name = f"test/e2e-{uuid4().hex}"

    first = _push(flow_services, source, name)
    session_count = flow_services.database.execute(
        text("SELECT count(*) FROM upload_sessions")
    ).scalar_one()
    replay = _push(flow_services, source, name)
    replay_session_count = flow_services.database.execute(
        text("SELECT count(*) FROM upload_sessions")
    ).scalar_one()

    assert replay.reference == first.reference
    assert replay.created_blob_count == 0
    assert replay.reused_blob_count == 2
    assert replay_session_count == session_count
    version = flow_services.control.resolve(first.reference)
    status = flow_services.control.status(version.id)
    assert status.snapshot.file_count == 3
    assert status.content.unique_blob_count == 2

    output = tmp_path / "restored"
    result = PullWorkflow(
        flow_services.control,
        AtomicTreeDownloader(flow_services.bytes),
    ).run(first.reference, output)

    assert result.materialized_file_count == 3
    assert _inventory(output) == _inventory(source)


@dataclass
class FailBeforeSecondPut:
    delegate: HttpByteTransfer
    calls: int = 0

    def put(self, *args: object, **kwargs: object) -> PutReceipt:
        self.calls += 1
        if self.calls == 2:
            raise RetryableTransferError("synthetic interruption")
        return self.delegate.put(*args, **kwargs)  # type: ignore[arg-type]


@dataclass
class CountingTransfer:
    delegate: HttpByteTransfer
    calls: int = 0

    def put(self, *args: object, **kwargs: object) -> PutReceipt:
        self.calls += 1
        return self.delegate.put(*args, **kwargs)  # type: ignore[arg-type]


def test_interrupted_push_resumes_without_reuploading_available_blob(
    flow_services: FlowServices, tmp_path: Path
) -> None:
    source = tmp_path / "resume"
    source.mkdir()
    (source / "a.bin").write_bytes(f"first-{uuid4()}".encode())
    (source / "b.bin").write_bytes(f"second-{uuid4()}".encode())
    name = f"test/resume-{uuid4().hex}"
    interrupted = FailBeforeSecondPut(flow_services.bytes)

    with pytest.raises(RetryableTransferError):
        _push(flow_services, source, name, interrupted)

    available_before = flow_services.database.execute(
        text("SELECT count(*) FROM blobs WHERE state = 'AVAILABLE'")
    ).scalar_one()
    counting = CountingTransfer(flow_services.bytes)
    resumed = _push(flow_services, source, name, counting)

    assert available_before == 1
    assert counting.calls == 1
    assert resumed.created_blob_count == 1
    assert resumed.reused_blob_count == 1


def test_post_ready_corruption_is_detected_without_final_output(
    flow_services: FlowServices, tmp_path: Path
) -> None:
    source = tmp_path / "corrupt-source"
    source.mkdir()
    data = f"ready-bytes-{uuid4()}".encode()
    (source / "data.bin").write_bytes(data)
    name = f"test/corrupt-{uuid4().hex}"
    pushed = _push(flow_services, source, name)
    digest = Sha256Digest.parse(hashlib.sha256(data).hexdigest())
    wrong = b"x" * len(data)
    wrong_digest = Sha256Digest.parse(hashlib.sha256(wrong).hexdigest())
    flow_services.objects.internal_client.put_object(
        Bucket=flow_services.objects.bucket,
        Key=object_key_for(digest),
        Body=wrong,
        ChecksumSHA256=wrong_digest.checksum_base64,
    )
    output = tmp_path / "must-not-exist"

    with pytest.raises(StoredObjectMismatchError):
        PullWorkflow(
            flow_services.control,
            AtomicTreeDownloader(flow_services.bytes),
        ).run(pushed.reference, output)

    assert not output.exists()
    assert flow_services.control.resolve(pushed.reference).state is VersionState.READY


def test_empty_directory_is_ready_and_pulls_to_empty_output(
    flow_services: FlowServices, tmp_path: Path
) -> None:
    source = tmp_path / "empty-source"
    source.mkdir()
    pushed = _push(flow_services, source, f"test/empty-{uuid4().hex}")
    output = tmp_path / "empty-output"

    pulled = PullWorkflow(
        flow_services.control,
        AtomicTreeDownloader(flow_services.bytes),
    ).run(pushed.reference, output)

    assert pushed.file_count == 0
    assert pushed.unique_blob_count == 0
    assert pulled.materialized_file_count == 0
    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_oversized_preflight_creates_no_registry_state(
    flow_services: FlowServices, tmp_path: Path
) -> None:
    source = tmp_path / "oversized"
    source.mkdir()
    (source / "too-large.bin").write_bytes(b"123456789")
    before = flow_services.database.execute(text("SELECT count(*) FROM datasets")).scalar_one()
    workflow = PushWorkflow(
        FileSystemScanner(max_single_put_bytes=8, chunk_size=2),
        flow_services.control,
        flow_services.bytes,
        lambda _: None,
    )

    with pytest.raises(UnsupportedFileSizeError):
        workflow.run(source, DatasetName.parse(f"test/oversized-{uuid4().hex}"))

    after = flow_services.database.execute(text("SELECT count(*) FROM datasets")).scalar_one()
    assert after == before
