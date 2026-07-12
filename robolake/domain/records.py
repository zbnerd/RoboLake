"""Framework-free immutable RoboLake records."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from robolake.domain.errors import ContentConflictError
from robolake.domain.identifiers import DatasetName, DatasetReference, Sha256Digest
from robolake.domain.lifecycle import (
    BlobState,
    FailureCode,
    NextAction,
    UploadSessionState,
    VersionState,
)


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    """Registered Dataset resource."""

    id: UUID
    name: DatasetName


@dataclass(frozen=True, slots=True)
class BlobRecord:
    """Content-addressed physical Blob resource."""

    id: UUID
    sha256: Sha256Digest
    size_bytes: int
    object_key: str
    state: BlobState
    failure_code: FailureCode | None = None


@dataclass(frozen=True, slots=True)
class UploadSessionRecord:
    """One persisted M1 single-PUT attempt."""

    id: UUID
    blob_id: UUID
    initiating_version_id: UUID
    state: UploadSessionState
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class VersionRecord:
    """Immutable representation of a registered DatasetVersion resource."""

    id: UUID
    dataset_id: UUID
    dataset_name: DatasetName
    version_number: int
    manifest_sha256: Sha256Digest
    state: VersionState
    file_count: int
    logical_bytes: int
    unique_blob_count: int
    unique_blob_bytes: int
    failure_code: FailureCode | None = None
    failure_detail: str | None = None

    @property
    def reference(self) -> DatasetReference:
        """Return the human registration-order reference."""
        return DatasetReference(self.dataset_name, self.version_number)

    @property
    def is_empty(self) -> bool:
        """Derive emptiness from the immutable logical file total."""
        return self.file_count == 0


@dataclass(frozen=True, slots=True)
class SnapshotStatus:
    """Logical file-tree totals and readiness."""

    file_count: int
    logical_bytes: int
    ready_file_count: int
    ready_logical_bytes: int

    def __post_init__(self) -> None:
        values = (
            self.file_count,
            self.logical_bytes,
            self.ready_file_count,
            self.ready_logical_bytes,
        )
        if any(value < 0 for value in values):
            raise ContentConflictError("Snapshot status totals must be nonnegative.")
        if self.ready_file_count > self.file_count or self.ready_logical_bytes > self.logical_bytes:
            raise ContentConflictError("Ready logical progress exceeds the Version total.")


@dataclass(frozen=True, slots=True)
class ContentStatus:
    """Deduplicated Blob totals and availability."""

    unique_blob_count: int
    unique_blob_bytes: int
    available_blob_count: int
    available_blob_bytes: int

    def __post_init__(self) -> None:
        values = (
            self.unique_blob_count,
            self.unique_blob_bytes,
            self.available_blob_count,
            self.available_blob_bytes,
        )
        if any(value < 0 for value in values):
            raise ContentConflictError("Content status totals must be nonnegative.")
        if (
            self.available_blob_count > self.unique_blob_count
            or self.available_blob_bytes > self.unique_blob_bytes
        ):
            raise ContentConflictError("Available Blob progress exceeds the Version total.")


@dataclass(frozen=True, slots=True)
class VersionStatus:
    """Derived status for one immutable DatasetVersion."""

    version: VersionRecord
    snapshot: SnapshotStatus
    content: ContentStatus
    blocking_failure_code: FailureCode | None = None

    @property
    def snapshot_completion_percent(self) -> float | None:
        """Return logical completion, with READY empty defined as complete."""
        if self.snapshot.file_count == 0:
            return 100.0 if self.version.state is VersionState.READY else None
        return self.snapshot.ready_file_count / self.snapshot.file_count * 100

    @property
    def content_completion_percent(self) -> float | None:
        """Return unique-content completion, with READY empty defined as complete."""
        if self.content.unique_blob_count == 0:
            return 100.0 if self.version.state is VersionState.READY else None
        return self.content.available_blob_count / self.content.unique_blob_count * 100

    @property
    def next_action(self) -> NextAction:
        """Derive caller guidance from publication state."""
        if self.blocking_failure_code is FailureCode.STORED_OBJECT_MISMATCH:
            return NextAction.CONTACT_OPERATOR
        if self.version.state is VersionState.READY:
            return NextAction.NONE
        if self.version.state is VersionState.VERIFYING:
            return NextAction.RETRY_STATUS
        return NextAction.RETRY_PUSH
