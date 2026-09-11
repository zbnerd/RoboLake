"""Framework-free application data contracts."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from robolake.domain.constants import MAX_PARTS
from robolake.domain.errors import PartChecksumRejectedError, ProviderContractError
from robolake.domain.identifiers import DatasetReference, RelativePath, Sha256Digest
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.multipart import sha256_base64_to_digest
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
class ProviderUploadId:
    """Opaque provider routing value that is always redacted from repr/str output."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or not self.value
            or any(unicodedata.category(character) == "Cc" for character in self.value)
        ):
            raise ProviderContractError("Provider multipart upload identifier is invalid.")


@dataclass(frozen=True, slots=True)
class UploadPartCapability:
    """One size/checksum-bound UploadPart capability."""

    part_number: int
    size_bytes: int
    expected_sha256: Sha256Digest
    request: PresignedRequest = field(repr=False)
    expires_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.part_number) is not int
            or not 1 <= self.part_number <= MAX_PARTS
            or type(self.size_bytes) is not int
            or self.size_bytes <= 0
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise ProviderContractError("UploadPart capability fields are invalid.")
        required = {
            "Content-Length": str(self.size_bytes),
            "x-amz-checksum-sha256": self.expected_sha256.checksum_base64,
        }
        if dict(self.request.headers) != required:
            raise ProviderContractError(
                "UploadPart capability headers do not match the frozen Part."
            )


@dataclass(frozen=True, slots=True)
class ListedProviderPart:
    """One provider ListParts observation, never an UploadPart response receipt."""

    part_number: int
    size_bytes: int
    etag: str
    checksum_sha256_base64: str
    modified_at: datetime | None

    def __post_init__(self) -> None:
        if (
            type(self.part_number) is not int
            or not 1 <= self.part_number <= MAX_PARTS
            or type(self.size_bytes) is not int
            or self.size_bytes <= 0
            or not isinstance(self.etag, str)
            or not self.etag
            or not isinstance(self.checksum_sha256_base64, str)
            or (
                self.modified_at is not None
                and (self.modified_at.tzinfo is None or self.modified_at.utcoffset() is None)
            )
        ):
            raise ProviderContractError("Provider ListParts observation is invalid.")
        try:
            sha256_base64_to_digest(self.checksum_sha256_base64)
        except PartChecksumRejectedError as error:
            raise ProviderContractError("Provider ListParts checksum is invalid.") from error


class MultipartCompletionOutcome(StrEnum):
    """Provider-neutral conditional completion outcomes."""

    COMPLETED = "COMPLETED"
    PRECONDITION_LOST = "PRECONDITION_LOST"
    CONDITIONAL_CONFLICT = "CONDITIONAL_CONFLICT"


@dataclass(frozen=True, slots=True)
class MultipartCompletionResult:
    outcome: MultipartCompletionOutcome


class MultipartAbortOutcome(StrEnum):
    """Provider-neutral confirmed abort outcomes."""

    ABORTED = "ABORTED"
    ALREADY_ABSENT = "ALREADY_ABSENT"


@dataclass(frozen=True, slots=True)
class MultipartAbortResult:
    outcome: MultipartAbortOutcome


@dataclass(frozen=True, slots=True)
class FinalObjectInspection:
    """Provider-owned final-key metadata with no canonical Blob digest claim."""

    exists: bool
    size_bytes: int | None
    etag: str | None
    provider_checksum_sha256: str | None
    provider_checksum_type: str | None

    def __post_init__(self) -> None:
        metadata = (
            self.size_bytes,
            self.etag,
            self.provider_checksum_sha256,
            self.provider_checksum_type,
        )
        if not self.exists:
            if any(value is not None for value in metadata):
                raise ProviderContractError("Missing final object cannot carry provider metadata.")
            return
        if type(self.size_bytes) is not int or self.size_bytes < 0 or not self.etag:
            raise ProviderContractError(
                "Final object inspection is missing required provider facts."
            )


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
