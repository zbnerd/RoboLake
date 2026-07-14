"""Pure M2 multipart protocol values and state machines."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, Self
from uuid import UUID

from robolake.domain.constants import (
    BASE_PART_BYTES,
    MAX_MULTIPART_BLOB_BYTES,
    MAX_PART_BYTES,
    MAX_PARTS,
    PART_PLAN_SCHEMA_VERSION,
)
from robolake.domain.errors import (
    ContentConflictError,
    IllegalTransitionError,
    InvalidDigestError,
    ManifestMismatchError,
    PartChecksumRejectedError,
    UnsupportedFileSizeError,
)
from robolake.domain.identifiers import Sha256Digest

MAX_SINGLE_PUT_BYTES = 5_000_000_000


@dataclass(frozen=True, slots=True)
class MultipartSessionId:
    value: UUID


@dataclass(frozen=True, slots=True)
class MultipartRequestId:
    value: UUID


@dataclass(frozen=True, slots=True)
class MultipartInvocationId:
    value: UUID


@dataclass(frozen=True, slots=True)
class AdmissionLeaseOwnerId:
    value: UUID


@dataclass(frozen=True, slots=True)
class CompletionLeaseOwnerId:
    value: UUID


@dataclass(frozen=True, slots=True)
class MultipartSessionGeneration:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value <= 0:
            raise ValueError("Multipart session generation must be positive.")


@dataclass(frozen=True, slots=True)
class AdmissionLeaseEpoch:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value <= 0:
            raise ValueError("Admission lease epoch must be positive.")


@dataclass(frozen=True, slots=True)
class CompletionLeaseEpoch:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value <= 0:
            raise ValueError("Completion lease epoch must be positive.")


@dataclass(frozen=True, slots=True)
class MultipartPart:
    """One deterministic byte range before its expected checksum is known."""

    part_number: int
    offset_bytes: int
    size_bytes: int

    def __post_init__(self) -> None:
        _require_protocol_integers(
            part_number=self.part_number,
            offset_bytes=self.offset_bytes,
            size_bytes=self.size_bytes,
        )
        if not 1 <= self.part_number <= MAX_PARTS:
            raise ManifestMismatchError("Multipart part number is outside 1..10,000.")
        if self.offset_bytes < 0 or self.size_bytes <= 0:
            raise ManifestMismatchError("Multipart part range must be positive and nonnegative.")


@dataclass(frozen=True, slots=True)
class MultipartPlan:
    """Selected deterministic boundaries for one Blob."""

    blob_size_bytes: int
    part_size_bytes: int
    parts: tuple[MultipartPart, ...]

    def __post_init__(self) -> None:
        _require_protocol_integers(
            blob_size_bytes=self.blob_size_bytes,
            part_size_bytes=self.part_size_bytes,
        )
        if self.blob_size_bytes <= 0 or self.blob_size_bytes > MAX_MULTIPART_BLOB_BYTES:
            raise ManifestMismatchError("Multipart Blob size is outside the protocol limit.")
        if not BASE_PART_BYTES <= self.part_size_bytes <= MAX_PART_BYTES:
            raise ManifestMismatchError("Selected multipart part size is outside protocol limits.")
        if self.part_size_bytes != _select_part_size(self.blob_size_bytes):
            raise ManifestMismatchError("Multipart part size differs from the protocol algorithm.")
        _validate_ranges(self.blob_size_bytes, self.part_size_bytes, self.parts)


def _require_protocol_integers(**values: int) -> None:
    if any(type(value) is not int for value in values.values()):
        raise ManifestMismatchError("Multipart protocol integer fields require exact int values.")


def _select_part_size(size_bytes: int) -> int:
    part_size = BASE_PART_BYTES
    while _ceil_div(size_bytes, part_size) > MAX_PARTS:
        part_size *= 2
        if part_size > MAX_PART_BYTES:
            raise UnsupportedFileSizeError("multipart", size_bytes)
    return part_size


def select_multipart_part_size(blob_size_bytes: int) -> int:
    """Select the one protocol part size for a valid M2 Blob."""
    if (
        type(blob_size_bytes) is not int
        or not MAX_SINGLE_PUT_BYTES < blob_size_bytes <= MAX_MULTIPART_BLOB_BYTES
    ):
        raise UnsupportedFileSizeError("multipart", blob_size_bytes)
    return _select_part_size(blob_size_bytes)


def _ceil_div(dividend: int, divisor: int) -> int:
    return (dividend + divisor - 1) // divisor


def plan_part_boundaries(size_bytes: int) -> MultipartPlan:
    """Build deterministic boundaries independent of M1/M2 routing selection."""
    if type(size_bytes) is not int or not 0 < size_bytes <= MAX_MULTIPART_BLOB_BYTES:
        raise UnsupportedFileSizeError("multipart", size_bytes)
    part_size = _select_part_size(size_bytes)
    count = _ceil_div(size_bytes, part_size)
    parts = tuple(
        MultipartPart(
            part_number=part_number,
            offset_bytes=(part_number - 1) * part_size,
            size_bytes=min(part_size, size_bytes - (part_number - 1) * part_size),
        )
        for part_number in range(1, count + 1)
    )
    return MultipartPlan(size_bytes, part_size, parts)


def plan_multipart(size_bytes: int) -> MultipartPlan:
    """Build the frozen M2 boundary plan for a Blob above the M1 threshold."""
    select_multipart_part_size(size_bytes)
    return plan_part_boundaries(size_bytes)


@dataclass(frozen=True, slots=True)
class PartDefinition:
    """One frozen part range and its canonical expected digest."""

    part_number: int
    offset_bytes: int
    size_bytes: int
    expected_sha256: Sha256Digest

    def __post_init__(self) -> None:
        MultipartPart(self.part_number, self.offset_bytes, self.size_bytes)


@dataclass(frozen=True, slots=True)
class PartPlan:
    """Canonical checksum-complete plan persisted before provider initiation."""

    schema_version: int
    blob_size_bytes: int
    part_size_bytes: int
    parts: tuple[PartDefinition, ...]

    def __post_init__(self) -> None:
        _require_protocol_integers(
            schema_version=self.schema_version,
            blob_size_bytes=self.blob_size_bytes,
            part_size_bytes=self.part_size_bytes,
        )
        if self.schema_version != PART_PLAN_SCHEMA_VERSION:
            raise ManifestMismatchError("Unsupported multipart PartPlan schema version.")
        if not MAX_SINGLE_PUT_BYTES < self.blob_size_bytes <= MAX_MULTIPART_BLOB_BYTES:
            raise ManifestMismatchError("PartPlan Blob size is outside protocol limits.")
        if self.part_size_bytes != select_multipart_part_size(self.blob_size_bytes):
            raise ManifestMismatchError("PartPlan part size differs from the protocol algorithm.")
        _validate_ranges(self.blob_size_bytes, self.part_size_bytes, self.parts)

    @classmethod
    def build(
        cls,
        *,
        schema_version: int,
        blob_size_bytes: int,
        part_size_bytes: int,
        parts: Iterable[PartDefinition],
    ) -> PartPlan:
        """Sort typed definitions before validating canonical range semantics."""
        ordered = tuple(sorted(parts, key=lambda item: item.part_number))
        return cls(schema_version, blob_size_bytes, part_size_bytes, ordered)

    @property
    def part_count(self) -> int:
        return len(self.parts)


class _PartRange(Protocol):
    @property
    def part_number(self) -> int: ...

    @property
    def offset_bytes(self) -> int: ...

    @property
    def size_bytes(self) -> int: ...


def _validate_ranges(
    blob_size_bytes: int,
    part_size_bytes: int,
    parts: Sequence[_PartRange],
) -> None:
    if not parts or len(parts) > MAX_PARTS:
        raise ManifestMismatchError("PartPlan must contain between 1 and 10,000 parts.")
    cursor = 0
    for expected_number, part in enumerate(parts, start=1):
        if part.part_number != expected_number or part.offset_bytes != cursor:
            raise ManifestMismatchError("PartPlan ranges must be consecutive and ordered.")
        if part.size_bytes > part_size_bytes:
            raise ManifestMismatchError("PartPlan part exceeds its frozen part size.")
        if expected_number < len(parts) and part.size_bytes != part_size_bytes:
            raise ManifestMismatchError("Every non-final PartPlan part must use the frozen size.")
        cursor += part.size_bytes
    if cursor != blob_size_bytes:
        raise ManifestMismatchError("PartPlan ranges must cover the Blob exactly once.")


def canonical_part_plan_bytes(plan: PartPlan) -> bytes:
    """Encode exact schema-v1 PartPlan bytes without trailing whitespace."""
    payload = {
        "schema_version": plan.schema_version,
        "blob_size_bytes": plan.blob_size_bytes,
        "part_size_bytes": plan.part_size_bytes,
        "part_count": plan.part_count,
        "parts": [
            {
                "part_number": part.part_number,
                "offset_bytes": part.offset_bytes,
                "size_bytes": part.size_bytes,
                "sha256": part.expected_sha256.value,
            }
            for part in plan.parts
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def part_plan_sha256(plan: PartPlan) -> Sha256Digest:
    """Hash exact canonical PartPlan bytes."""
    return Sha256Digest(hashlib.sha256(canonical_part_plan_bytes(plan)).hexdigest())


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestMismatchError("PartPlan JSON contains duplicate keys.")
        result[key] = value
    return result


def parse_canonical_part_plan(data: bytes) -> PartPlan:
    """Parse, validate, and reproduce an exact canonical PartPlan."""
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestMismatchError("PartPlan must be canonical UTF-8 JSON.") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "blob_size_bytes",
        "part_size_bytes",
        "part_count",
        "parts",
    }:
        raise ManifestMismatchError("PartPlan top-level fields are invalid.")
    integer_fields = ("schema_version", "blob_size_bytes", "part_size_bytes", "part_count")
    if any(
        not isinstance(payload[field], int) or isinstance(payload[field], bool)
        for field in integer_fields
    ) or not isinstance(payload["parts"], list):
        raise ManifestMismatchError("PartPlan numeric and parts fields are invalid.")

    definitions: list[PartDefinition] = []
    for item in payload["parts"]:
        if not isinstance(item, dict) or set(item) != {
            "part_number",
            "offset_bytes",
            "size_bytes",
            "sha256",
        }:
            raise ManifestMismatchError("PartPlan part fields are invalid.")
        if any(
            not isinstance(item[field], int) or isinstance(item[field], bool)
            for field in ("part_number", "offset_bytes", "size_bytes")
        ) or not isinstance(item["sha256"], str):
            raise ManifestMismatchError("PartPlan part values are invalid.")
        try:
            digest = Sha256Digest(item["sha256"])
        except InvalidDigestError as error:
            raise ManifestMismatchError("PartPlan digest is not canonical.") from error
        definitions.append(
            PartDefinition(
                part_number=item["part_number"],
                offset_bytes=item["offset_bytes"],
                size_bytes=item["size_bytes"],
                expected_sha256=digest,
            )
        )
    plan = PartPlan.build(
        schema_version=payload["schema_version"],
        blob_size_bytes=payload["blob_size_bytes"],
        part_size_bytes=payload["part_size_bytes"],
        parts=definitions,
    )
    if payload["part_count"] != plan.part_count or canonical_part_plan_bytes(plan) != data:
        raise ManifestMismatchError("PartPlan bytes are not canonical schema version 1.")
    return plan


def sha256_digest_to_base64(digest: Sha256Digest) -> str:
    """Encode a canonical digest as padded RFC 4648 Base64 raw bytes."""
    return base64.b64encode(digest.raw_bytes).decode("ascii")


def sha256_base64_to_digest(value: str) -> Sha256Digest:
    """Decode only canonical padded Base64 containing exactly 32 raw bytes."""
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as error:
        raise PartChecksumRejectedError(
            "Provider SHA-256 checksum is not canonical Base64."
        ) from error
    if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != value:
        raise PartChecksumRejectedError("Provider SHA-256 checksum must encode 32 raw bytes.")
    return Sha256Digest(raw.hex())


@dataclass(frozen=True, slots=True)
class CompletedPartReceipt:
    """Opaque successful UploadPart response fields required by RoboLake's profile."""

    part_number: int
    response_etag: str
    response_checksum_sha256_base64: str

    def __post_init__(self) -> None:
        _require_protocol_integers(part_number=self.part_number)
        if not 1 <= self.part_number <= MAX_PARTS:
            raise ContentConflictError("CompletedPart receipt number is outside 1..10,000.")
        if not self.response_etag:
            raise ContentConflictError("CompletedPart receipt requires a response ETag.")
        sha256_base64_to_digest(self.response_checksum_sha256_base64)

    @classmethod
    def from_upload_response(
        cls,
        part_number: int,
        response_etag: str,
        response_checksum_sha256_base64: str,
        expected_sha256: Sha256Digest,
    ) -> CompletedPartReceipt:
        receipt = cls(part_number, response_etag, response_checksum_sha256_base64)
        return receipt.validate_for(expected_sha256)

    def validate_for(self, expected_sha256: Sha256Digest) -> Self:
        if sha256_base64_to_digest(self.response_checksum_sha256_base64) != expected_sha256:
            raise PartChecksumRejectedError("UploadPart response checksum differs from the plan.")
        return self


class MultipartSessionState(StrEnum):
    CREATED = "CREATED"
    INITIATING = "INITIATING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    ABORTING = "ABORTING"
    ABORTED = "ABORTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class UploadPartState(StrEnum):
    PENDING = "PENDING"
    UPLOADED = "UPLOADED"
    VERIFIED = "VERIFIED"


class CompletionPhase(StrEnum):
    PENDING = "PENDING"
    ASSEMBLING = "ASSEMBLING"
    FINAL_PRESENT = "FINAL_PRESENT"
    FINAL_VERIFICATION = "FINAL_VERIFICATION"


class CompletionReason(StrEnum):
    PARTS_READY = "PARTS_READY"
    FINAL_PRESENT = "FINAL_PRESENT"


class CompletionRecoveryReason(StrEnum):
    PARTS_READY = "PARTS_READY"
    PARTS_PARTIAL = "PARTS_PARTIAL"
    CONDITIONAL_409 = "CONDITIONAL_409"
    MULTIPART_SESSION_NOT_FOUND = "MULTIPART_SESSION_NOT_FOUND"


class CompletionRecoveryAction(StrEnum):
    RETRY_COMPLETION = "RETRY_COMPLETION"
    RESUME_PART_UPLOAD = "RESUME_PART_UPLOAD"
    START_NEW_GENERATION = "START_NEW_GENERATION"


class MultipartFailureReason(StrEnum):
    INITIATION_AMBIGUOUS = "INITIATION_AMBIGUOUS"
    PROVIDER_ATTEMPT_INVALIDATED = "PROVIDER_ATTEMPT_INVALIDATED"
    STORED_OBJECT_MISMATCH = "STORED_OBJECT_MISMATCH"
    PLAN_INVARIANT = "PLAN_INVARIANT"


class VerificationMethod(StrEnum):
    FULL_STREAM_SHA256 = "FULL_STREAM_SHA256"


class PartAttribution(StrEnum):
    NEWLY_TRANSFERRED = "NEWLY_TRANSFERRED"
    REUSED_PROVIDER = "REUSED_PROVIDER"
    RECONCILED = "RECONCILED"


_MULTIPART_SESSION_EDGES = {
    (MultipartSessionState.CREATED, MultipartSessionState.INITIATING),
    (MultipartSessionState.CREATED, MultipartSessionState.CANCELLED),
    (MultipartSessionState.CREATED, MultipartSessionState.FAILED),
    (MultipartSessionState.INITIATING, MultipartSessionState.IN_PROGRESS),
    (MultipartSessionState.INITIATING, MultipartSessionState.FAILED),
    (MultipartSessionState.IN_PROGRESS, MultipartSessionState.COMPLETING),
    (MultipartSessionState.IN_PROGRESS, MultipartSessionState.ABORTING),
    (MultipartSessionState.IN_PROGRESS, MultipartSessionState.FAILED),
    (MultipartSessionState.COMPLETING, MultipartSessionState.COMPLETED),
    (MultipartSessionState.COMPLETING, MultipartSessionState.FAILED),
    (MultipartSessionState.ABORTING, MultipartSessionState.ABORTED),
    (MultipartSessionState.ABORTING, MultipartSessionState.COMPLETING),
    (MultipartSessionState.ABORTING, MultipartSessionState.FAILED),
}


def require_multipart_session_transition(
    current: MultipartSessionState,
    target: MultipartSessionState,
    *,
    guarded_completion_recovery: bool = False,
) -> MultipartSessionState:
    if current == target or (current, target) in _MULTIPART_SESSION_EDGES:
        return target
    if (
        guarded_completion_recovery
        and current is MultipartSessionState.COMPLETING
        and target is MultipartSessionState.IN_PROGRESS
    ):
        return target
    raise IllegalTransitionError(f"Multipart session cannot transition from {current} to {target}.")


def require_upload_part_transition(
    current: UploadPartState,
    target: UploadPartState,
    *,
    provider_reconciled: bool = False,
) -> UploadPartState:
    if current == target or (current, target) in {
        (UploadPartState.PENDING, UploadPartState.UPLOADED),
        (UploadPartState.UPLOADED, UploadPartState.VERIFIED),
    }:
        return target
    if (
        provider_reconciled
        and current in {UploadPartState.UPLOADED, UploadPartState.VERIFIED}
        and target is UploadPartState.PENDING
    ):
        return target
    raise IllegalTransitionError(f"UploadPart cannot transition from {current} to {target}.")


_COMPLETION_PHASE_EDGES = {
    (CompletionPhase.PENDING, CompletionPhase.ASSEMBLING),
    (CompletionPhase.ASSEMBLING, CompletionPhase.FINAL_PRESENT),
    (CompletionPhase.FINAL_PRESENT, CompletionPhase.FINAL_VERIFICATION),
}


def require_completion_phase_transition(
    current: CompletionPhase,
    target: CompletionPhase,
    *,
    final_present_observed: bool = False,
    retryable_reconciliation: bool = False,
    transient_read_failure: bool = False,
) -> CompletionPhase:
    if current == target or (current, target) in _COMPLETION_PHASE_EDGES:
        return target
    if (
        final_present_observed
        and current is CompletionPhase.PENDING
        and target is CompletionPhase.FINAL_PRESENT
    ):
        return target
    if (
        retryable_reconciliation
        and current is CompletionPhase.ASSEMBLING
        and target is CompletionPhase.PENDING
    ):
        return target
    if (
        transient_read_failure
        and current is CompletionPhase.FINAL_VERIFICATION
        and target is CompletionPhase.FINAL_PRESENT
    ):
        return target
    raise IllegalTransitionError(f"Completion phase cannot transition from {current} to {target}.")
