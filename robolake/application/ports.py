"""Framework-free ports implemented by RoboLake adapters."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol
from uuid import UUID

from robolake.application.contracts import (
    DownloadCapability,
    DownloadItem,
    LocalFileRef,
    ObjectInfo,
    PreparedDownload,
    PresignedRequest,
    PutReceipt,
    ScannedDataset,
    UploadContext,
    UploadPreparation,
)
from robolake.domain.identifiers import DatasetName, DatasetReference, Sha256Digest
from robolake.domain.lifecycle import FailureCode
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import DatasetRecord, VersionRecord, VersionStatus


class RegistryStore(Protocol):
    """Atomic registry persistence boundary."""

    def create_dataset(
        self, name: DatasetName, idempotency_key: str, request_sha256: Sha256Digest
    ) -> DatasetRecord: ...

    def register_version(
        self,
        dataset_id: UUID,
        manifest: Manifest,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> VersionRecord: ...

    def resolve_version(self, reference: DatasetReference) -> VersionRecord: ...

    def get_manifest(self, version_id: UUID) -> Manifest: ...

    def get_status(self, version_id: UUID) -> VersionStatus: ...

    def prepare_upload(
        self,
        version_id: UUID,
        sha256: Sha256Digest,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> UploadContext: ...

    def get_upload(self, session_id: UUID) -> UploadContext: ...

    def mark_upload_in_progress(self, session_id: UUID) -> UploadContext: ...

    def restart_failed_upload(
        self,
        version_id: UUID,
        blob_id: UUID,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> UploadContext: ...

    def mark_upload_verified(self, session_id: UUID, etag: str | None) -> UploadContext: ...

    def mark_upload_failed(
        self, session_id: UUID, code: FailureCode, detail: str
    ) -> UploadContext: ...

    def finalize_version(self, version_id: UUID) -> VersionRecord: ...

    def get_download_entry(
        self, version_id: UUID, manifest_ordinal: int
    ) -> ManifestEntry | None: ...


class ObjectStorePort(Protocol):
    """S3-compatible control and server-side verification boundary."""

    def head(self, object_key: str) -> ObjectInfo | None: ...

    def iter_bytes(self, object_key: str, chunk_size: int) -> Iterator[bytes]: ...

    def presign_put(
        self,
        object_key: str,
        size_bytes: int,
        checksum_base64: str,
        expires_seconds: int,
    ) -> PresignedRequest: ...

    def presign_get(self, object_key: str, expires_seconds: int) -> PresignedRequest: ...


class ScannerPort(Protocol):
    def scan(self, source: Path) -> ScannedDataset: ...


class ControlPlanePort(Protocol):
    def create_dataset(self, name: DatasetName, idempotency_key: str) -> DatasetRecord: ...

    def register_version(
        self, dataset_id: UUID, manifest: Manifest, idempotency_key: str
    ) -> VersionRecord: ...

    def resolve(self, reference: DatasetReference) -> VersionRecord: ...

    def status(self, version_id: UUID) -> VersionStatus: ...

    def manifest(self, version_id: UUID) -> Manifest: ...

    def prepare_upload(
        self, version_id: UUID, digest: Sha256Digest, idempotency_key: str
    ) -> UploadPreparation: ...

    def upload_url(self, session_id: UUID) -> PresignedRequest: ...

    def complete_upload(self, session_id: UUID, etag: str | None) -> UploadPreparation: ...

    def finalize(self, version_id: UUID) -> VersionRecord: ...

    def download_plan(self, version_id: UUID, cursor: str | None) -> DownloadCapability: ...


class ByteTransferPort(Protocol):
    def put(
        self,
        local_ref: LocalFileRef,
        request: PresignedRequest,
        expected_entry: ManifestEntry,
    ) -> PutReceipt: ...


class TreeDownloaderPort(Protocol):
    def begin(self, output: Path, manifest: Manifest) -> None: ...

    def prepare(self, entry: ManifestEntry) -> PreparedDownload: ...

    def write(self, prepared: PreparedDownload, item: DownloadItem) -> None: ...

    def commit(self) -> Path: ...

    def abort(self) -> None: ...
