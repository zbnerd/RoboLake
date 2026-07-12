"""Dataset and immutable Version registry use cases."""

from __future__ import annotations

from uuid import UUID

from robolake.application.idempotency import request_fingerprint
from robolake.application.ports import RegistryStore
from robolake.domain.identifiers import DatasetName, DatasetReference
from robolake.domain.manifest import Manifest
from robolake.domain.records import DatasetRecord, VersionRecord, VersionStatus


class RegistryService:
    """Coordinate framework-free registry operations through an atomic store."""

    def __init__(self, store: RegistryStore) -> None:
        self._store = store

    def create_dataset(self, name: DatasetName, idempotency_key: str) -> DatasetRecord:
        """Create or replay one Dataset request."""
        fingerprint = request_fingerprint([name.value.encode("utf-8")])
        return self._store.create_dataset(name, idempotency_key, fingerprint)

    def register_version(
        self, dataset_id: UUID, manifest: Manifest, idempotency_key: str
    ) -> VersionRecord:
        """Register or replay one already-canonical immutable manifest."""
        fingerprint = request_fingerprint(
            [dataset_id.bytes, manifest.sha256.raw_bytes, manifest.canonical_bytes]
        )
        return self._store.register_version(dataset_id, manifest, idempotency_key, fingerprint)

    def resolve(self, reference: DatasetReference) -> VersionRecord:
        """Resolve a human Dataset reference."""
        return self._store.resolve_version(reference)

    def status(self, version_id: UUID) -> VersionStatus:
        """Read derived logical and physical progress."""
        return self._store.get_status(version_id)

    def manifest(self, version_id: UUID) -> Manifest:
        """Read and independently validate persisted canonical bytes."""
        stored = self._store.get_manifest(version_id)
        return Manifest.from_canonical_bytes(stored.canonical_bytes)
