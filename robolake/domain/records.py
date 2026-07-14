"""Framework-free immutable RoboLake records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
from robolake.domain.multipart import (
    AdmissionLeaseEpoch,
    AdmissionLeaseOwnerId,
    CompletedPartReceipt,
    CompletionLeaseEpoch,
    CompletionLeaseOwnerId,
    CompletionPhase,
    MultipartInvocationId,
    MultipartSessionGeneration,
    MultipartSessionId,
    MultipartSessionState,
    PartAttribution,
    PartDefinition,
    PartPlan,
    UploadPartState,
    VerificationMethod,
    part_plan_sha256,
    sha256_base64_to_digest,
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
class MultipartUploadSessionRecord:
    """One generation-numbered immutable multipart attempt."""

    id: MultipartSessionId
    blob_id: UUID
    initiating_version_id: UUID
    generation: MultipartSessionGeneration
    state: MultipartSessionState
    part_plan: PartPlan
    provider_upload_id: str | None = field(default=None, repr=False)
    completion_phase: CompletionPhase | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class UploadPartRecord:
    """One planned multipart range plus mutable provider progress facts."""

    session_id: MultipartSessionId
    definition: PartDefinition
    state: UploadPartState
    response_receipt: CompletedPartReceipt | None = None


@dataclass(frozen=True, slots=True)
class ProviderPartObservation:
    """One current ListParts fact; never a substitute for an upload receipt."""

    part_number: int
    size_bytes: int
    etag: str
    checksum_sha256_base64: str

    def __post_init__(self) -> None:
        if self.part_number <= 0 or self.size_bytes <= 0 or not self.etag:
            raise ContentConflictError("Provider Part observation is incomplete.")
        sha256_base64_to_digest(self.checksum_sha256_base64)


@dataclass(frozen=True, slots=True)
class PartReconciliationResult:
    """Invocation attribution derived from one complete provider reconciliation."""

    attribution: PartAttribution
    resolved_part_count: int
    resolved_part_bytes: int

    def __post_init__(self) -> None:
        if self.resolved_part_count < 0 or self.resolved_part_bytes < 0:
            raise ContentConflictError("Reconciled Part totals must be nonnegative.")


@dataclass(frozen=True, slots=True)
class PartialCompletionEvidence:
    """Provider observations required by guarded same-MPU recovery."""

    final_absence_observed_at: datetime
    provider_reconciled_at: datetime
    provider_parts: tuple[ProviderPartObservation, ...]
    completion_result: str = "AMBIGUOUS"

    def __post_init__(self) -> None:
        _require_aware(self.final_absence_observed_at, "final_absence_observed_at")
        _require_aware(self.provider_reconciled_at, "provider_reconciled_at")
        if self.completion_result not in {"AMBIGUOUS", "EMBEDDED_ERROR"}:
            raise ContentConflictError("Completion evidence is not eligible for same-MPU recovery.")


@dataclass(frozen=True, slots=True)
class HashedMultipartPlan:
    """A canonical PartPlan paired with its immutable protocol digest."""

    plan: PartPlan
    sha256: Sha256Digest

    def __post_init__(self) -> None:
        if part_plan_sha256(self.plan) != self.sha256:
            raise ContentConflictError("PartPlan hash differs from its canonical bytes.")

    @classmethod
    def from_plan(cls, plan: PartPlan) -> HashedMultipartPlan:
        return cls(plan=plan, sha256=part_plan_sha256(plan))


@dataclass(frozen=True, slots=True)
class MultipartUploadContext:
    """Persisted session, Blob identity, and immutable Part rows."""

    session: MultipartUploadSessionRecord
    blob: BlobRecord
    parts: tuple[UploadPartRecord, ...]


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContentConflictError(f"{field_name} must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class AdmissionLeaseRecord:
    """Temporary upload admission ownership independent of session lifetime."""

    session_id: MultipartSessionId
    invocation_id: MultipartInvocationId
    lease_owner_id: AdmissionLeaseOwnerId
    lease_epoch: AdmissionLeaseEpoch
    expires_at: datetime
    last_renewed_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.expires_at, "expires_at")
        _require_aware(self.last_renewed_at, "last_renewed_at")


@dataclass(frozen=True, slots=True)
class CompletionLeaseRecord:
    """Server-side completion ownership independent of upload admission."""

    session_id: MultipartSessionId
    lease_owner_id: CompletionLeaseOwnerId
    lease_epoch: CompletionLeaseEpoch
    expires_at: datetime
    last_heartbeat_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.expires_at, "expires_at")
        _require_aware(self.last_heartbeat_at, "last_heartbeat_at")


@dataclass(frozen=True, slots=True)
class AcceptedCompletion:
    """Durable idempotent acceptance of asynchronous completion work."""

    request_id: UUID
    session_id: MultipartSessionId
    http_status: int = 202


@dataclass(frozen=True, slots=True)
class CompletionClaim:
    """Current fenced ownership of one durable completion work item."""

    session_id: MultipartSessionId
    lease: CompletionLeaseRecord
    phase: CompletionPhase


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """Immutable application-recorded evidence for the short publication gate."""

    verification_method: VerificationMethod
    observed_sha256: Sha256Digest
    observed_size_bytes: int
    verification_read_bytes: int
    verification_completed_at: datetime
    verifier_version: str

    def __post_init__(self) -> None:
        _require_aware(self.verification_completed_at, "verification_completed_at")
        if self.observed_size_bytes < 0 or self.verification_read_bytes < 0:
            raise ContentConflictError("Verification byte counts must be nonnegative.")
        if not self.verifier_version or len(self.verifier_version) > 128:
            raise ContentConflictError("Verifier version must contain 1..128 characters.")

    def validate_for(self, digest: Sha256Digest, size_bytes: int) -> VerificationEvidence:
        """Require evidence structure to equal immutable Blob identity."""
        if self.verification_method is not VerificationMethod.FULL_STREAM_SHA256:
            raise ContentConflictError("Unsupported verification evidence method.")
        if (
            self.observed_sha256 != digest
            or self.observed_size_bytes != size_bytes
            or self.verification_read_bytes != size_bytes
        ):
            raise ContentConflictError("Verification evidence differs from Blob identity.")
        return self


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
