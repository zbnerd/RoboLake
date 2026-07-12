"""Behavior tests for the framework-free registry use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from robolake.application.idempotency import request_fingerprint
from robolake.application.registry import RegistryService
from robolake.domain.errors import ManifestMismatchError
from robolake.domain.identifiers import DatasetName, DatasetReference, RelativePath, Sha256Digest
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import (
    ContentStatus,
    DatasetRecord,
    SnapshotStatus,
    VersionRecord,
    VersionStatus,
)


def _manifest() -> Manifest:
    return Manifest.build(
        [
            ManifestEntry(
                RelativePath.parse("camera/front.bin"),
                3,
                Sha256Digest.parse("a" * 64),
            )
        ]
    )


@dataclass
class RecordingRegistryStore:
    dataset: DatasetRecord
    version: VersionRecord
    stored_manifest: Manifest
    status: VersionStatus
    create_call: tuple[DatasetName, str, Sha256Digest] | None = field(default=None, init=False)
    register_call: tuple[UUID, Manifest, str, Sha256Digest] | None = field(default=None, init=False)

    def create_dataset(
        self, name: DatasetName, idempotency_key: str, request_sha256: Sha256Digest
    ) -> DatasetRecord:
        self.create_call = (name, idempotency_key, request_sha256)
        return self.dataset

    def register_version(
        self,
        dataset_id: UUID,
        manifest: Manifest,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> VersionRecord:
        self.register_call = (dataset_id, manifest, idempotency_key, request_sha256)
        return self.version

    def resolve_version(self, reference: DatasetReference) -> VersionRecord:
        assert reference == self.version.reference
        return self.version

    def get_manifest(self, version_id: UUID) -> Manifest:
        assert version_id == self.version.id
        return self.stored_manifest

    def get_status(self, version_id: UUID) -> VersionStatus:
        assert version_id == self.version.id
        return self.status


def _store(stored_manifest: Manifest | None = None) -> RecordingRegistryStore:
    manifest = _manifest()
    dataset = DatasetRecord(uuid4(), DatasetName.parse("demo/registry"))
    version = VersionRecord(
        id=uuid4(),
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        version_number=1,
        manifest_sha256=manifest.sha256,
        state=VersionState.DRAFT,
        file_count=manifest.file_count,
        logical_bytes=manifest.logical_bytes,
        unique_blob_count=manifest.unique_blob_count,
        unique_blob_bytes=manifest.unique_blob_bytes,
    )
    status = VersionStatus(
        version,
        SnapshotStatus(1, 3, 0, 0),
        ContentStatus(1, 3, 0, 0),
    )
    return RecordingRegistryStore(dataset, version, stored_manifest or manifest, status)


def test_registry_service_supplies_payload_bound_fingerprints() -> None:
    store = _store()
    service = RegistryService(store)

    dataset = service.create_dataset(store.dataset.name, "caller-create-key")
    version = service.register_version(
        store.dataset.id, store.stored_manifest, "caller-version-key"
    )

    assert dataset == store.dataset
    assert version == store.version
    assert store.create_call == (
        store.dataset.name,
        "caller-create-key",
        request_fingerprint([store.dataset.name.value.encode("utf-8")]),
    )
    assert store.register_call == (
        store.dataset.id,
        store.stored_manifest,
        "caller-version-key",
        request_fingerprint(
            [
                store.dataset.id.bytes,
                store.stored_manifest.sha256.raw_bytes,
                store.stored_manifest.canonical_bytes,
            ]
        ),
    )


def test_registry_reads_resolve_status_and_reparse_canonical_manifest() -> None:
    store = _store()
    service = RegistryService(store)

    assert service.resolve(store.version.reference) == store.version
    assert service.status(store.version.id) == store.status
    assert service.manifest(store.version.id) == store.stored_manifest


def test_registry_manifest_rejects_corrupt_persisted_bytes() -> None:
    valid = _manifest()
    corrupt = Manifest(valid.entries, b"{}", valid.sha256)
    service = RegistryService(_store(corrupt))

    with pytest.raises(ManifestMismatchError):
        service.manifest(service.resolve(DatasetReference.parse("demo/registry@v1")).id)
