"""Framework-free application data contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from robolake.domain.identifiers import DatasetReference, RelativePath, Sha256Digest
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import BlobRecord, UploadSessionRecord, VersionRecord


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Filesystem identity captured around a safe read."""

    device: int
    inode: int
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class LocalFileRef:
    """CLI-local source reference that is never serialized."""

    root: Path = field(repr=False)
    root_identity: FileIdentity
    raw_parts: tuple[str, ...]
    file_identity: FileIdentity


@dataclass(frozen=True, slots=True)
class ScannedFile:
    """Manifest entry paired with its local-only source reference."""

    entry: ManifestEntry
    local_ref: LocalFileRef = field(repr=False)


@dataclass(frozen=True, slots=True)
class ScannedDataset:
    """Complete result of one stable local scan."""

    root: Path = field(repr=False)
    manifest: Manifest
    files: tuple[ScannedFile, ...]


@dataclass(frozen=True, slots=True)
class UploadContext:
    """Persisted state needed to decide one Blob transfer."""

    version: VersionRecord
    blob: BlobRecord
    session: UploadSessionRecord | None


@dataclass(frozen=True, slots=True)
class UploadPreparation:
    """Safe client-facing result of upload preparation or completion."""

    version: VersionRecord
    blob_sha256: Sha256Digest
    size_bytes: int
    available: bool
    session_id: UUID | None


@dataclass(frozen=True, slots=True)
class PresignedRequest:
    """Bearer request whose secret material must never appear in repr output."""

    url: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class DownloadItem:
    """One logical entry paired with a short-lived GET capability."""

    entry: ManifestEntry
    request: PresignedRequest


@dataclass(frozen=True, slots=True)
class DownloadCapability:
    """At most one M1 download capability and its navigation state."""

    item: DownloadItem | None
    next_cursor: str | None
    complete: bool


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """Provider-owned integrity facts for one stored object."""

    size_bytes: int
    checksum_sha256: Sha256Digest | None
    etag: str | None


class PutOutcome(StrEnum):
    CREATED = "CREATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class PutReceipt:
    outcome: PutOutcome
    etag: str | None


class ProgressKind(StrEnum):
    SCANNED = "SCANNED"
    UPLOADING = "UPLOADING"
    SKIPPED = "SKIPPED"
    VERIFIED = "VERIFIED"
    FINALIZING = "FINALIZING"
    DOWNLOADING = "DOWNLOADING"
    RESTORED = "RESTORED"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    kind: ProgressKind
    relative_path: RelativePath | None
    resolved_blob_count: int
    unique_blob_count: int
    resolved_blob_bytes: int
    unique_blob_bytes: int


@dataclass(frozen=True, slots=True)
class PushResult:
    reference: DatasetReference
    state: VersionState
    file_count: int
    logical_bytes: int
    unique_blob_count: int
    unique_blob_bytes: int
    created_blob_count: int
    created_blob_bytes: int
    reused_blob_count: int
    reused_blob_bytes: int


@dataclass(frozen=True, slots=True)
class PreparedDownload:
    """Opaque local staging handle that never crosses the control plane."""

    token: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class PullResult:
    reference: DatasetReference
    output: Path
    materialized_file_count: int
    materialized_bytes: int
