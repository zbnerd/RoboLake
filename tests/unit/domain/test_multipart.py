"""Pure-domain tests for the frozen M2 multipart protocol."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from robolake.domain.constants import (
    BASE_PART_BYTES,
    MAX_MULTIPART_BLOB_BYTES,
    MAX_PARTS,
    PART_PLAN_SCHEMA_VERSION,
)
from robolake.domain.errors import (
    AdmissionCapacityExhaustedError,
    AdmissionLeaseHeldError,
    AdmissionLeaseLostError,
    ContentConflictError,
    FinalBlobPublicationConflictError,
    IllegalTransitionError,
    InvalidPartNumberError,
    LocalFileChangedError,
    ManifestMismatchError,
    MultipartCompletionAmbiguousError,
    MultipartInitiationAmbiguousError,
    MultipartInitiationInProgressError,
    MultipartSessionNotFoundError,
    PartChecksumRejectedError,
    PartSizeMismatchError,
    UnsupportedFileSizeError,
)
from robolake.domain.identifiers import Sha256Digest
from robolake.domain.multipart import (
    AdmissionLeaseEpoch,
    AdmissionLeaseOwnerId,
    CompletedPartReceipt,
    CompletionLeaseEpoch,
    CompletionLeaseOwnerId,
    CompletionPhase,
    MultipartInvocationId,
    MultipartRequestId,
    MultipartSessionGeneration,
    MultipartSessionId,
    MultipartSessionState,
    PartDefinition,
    PartPlan,
    UploadPartState,
    VerificationMethod,
    canonical_part_plan_bytes,
    parse_canonical_part_plan,
    part_plan_sha256,
    plan_multipart,
    plan_part_boundaries,
    require_completion_phase_transition,
    require_multipart_session_transition,
    require_upload_part_transition,
    sha256_base64_to_digest,
    sha256_digest_to_base64,
)
from robolake.domain.records import (
    AdmissionLeaseRecord,
    CompletionLeaseRecord,
    HashedMultipartPlan,
    PartialCompletionEvidence,
    ProviderPartObservation,
    VerificationEvidence,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("size", "part_size", "count", "final_size"),
    [
        (5_000_000_001, 67_108_864, 75, 33_944_065),
        (10 * 1024**3, 67_108_864, 160, 67_108_864),
        (100 * 1024**3, 67_108_864, 1_600, 67_108_864),
        (1024**4, 134_217_728, 8_192, 134_217_728),
        (5_000_000_000_000, 536_870_912, 9_314, 121_196_544),
    ],
)
def test_plan_multipart_protocol_boundaries(
    size: int, part_size: int, count: int, final_size: int
) -> None:
    plan = plan_multipart(size)

    assert plan.blob_size_bytes == size
    assert plan.part_size_bytes == part_size
    assert len(plan.parts) == count
    assert plan.parts[-1].size_bytes == final_size
    assert sum(part.size_bytes for part in plan.parts) == size


def test_boundary_builder_covers_base_exactly_and_one_byte_beyond() -> None:
    exact = plan_part_boundaries(BASE_PART_BYTES)
    plus_one = plan_part_boundaries(BASE_PART_BYTES + 1)

    assert [(part.offset_bytes, part.size_bytes) for part in exact.parts] == [(0, BASE_PART_BYTES)]
    assert [(part.offset_bytes, part.size_bytes) for part in plus_one.parts] == [
        (0, BASE_PART_BYTES),
        (BASE_PART_BYTES, 1),
    ]


@pytest.mark.parametrize(
    "size",
    [
        5_000_000_001,
        BASE_PART_BYTES * MAX_PARTS,
        BASE_PART_BYTES * MAX_PARTS + 1,
        10 * 1024**3,
        100 * 1024**3,
        1024**4,
        MAX_MULTIPART_BLOB_BYTES,
    ],
)
def test_every_selected_plan_covers_blob_once_without_gaps(size: int) -> None:
    plan = plan_multipart(size)

    cursor = 0
    for expected_number, part in enumerate(plan.parts, start=1):
        assert part.part_number == expected_number
        assert part.offset_bytes == cursor
        assert 0 < part.size_bytes <= plan.part_size_bytes
        cursor += part.size_bytes
    assert cursor == size
    assert len(plan.parts) <= MAX_PARTS


@pytest.mark.parametrize(
    "size",
    [
        0,
        -1,
        5_000_000_000,
        5 * 1024**4,
        MAX_MULTIPART_BLOB_BYTES + 1,
    ],
)
def test_plan_multipart_rejects_sizes_outside_m2_protocol(size: int) -> None:
    with pytest.raises(UnsupportedFileSizeError):
        plan_multipart(size)


def test_exact_ten_thousand_part_boundary_keeps_base_part_size() -> None:
    plan = plan_multipart(BASE_PART_BYTES * MAX_PARTS)

    assert plan.part_size_bytes == BASE_PART_BYTES
    assert len(plan.parts) == MAX_PARTS


def _compact_plan() -> PartPlan:
    return PartPlan.build(
        schema_version=PART_PLAN_SCHEMA_VERSION,
        blob_size_bytes=12_582_912,
        part_size_bytes=6_291_456,
        parts=(
            PartDefinition(1, 0, 6_291_456, Sha256Digest("1" * 64)),
            PartDefinition(2, 6_291_456, 6_291_456, Sha256Digest("2" * 64)),
        ),
    )


def test_part_plan_canonical_bytes_match_the_accepted_golden_vector() -> None:
    expected = (
        b'{"schema_version":1,"blob_size_bytes":12582912,"part_size_bytes":6291456,'
        b'"part_count":2,"parts":[{"part_number":1,"offset_bytes":0,'
        b'"size_bytes":6291456,"sha256":"1111111111111111111111111111111111111111111111111111111111111111"},'
        b'{"part_number":2,"offset_bytes":6291456,"size_bytes":6291456,'
        b'"sha256":"2222222222222222222222222222222222222222222222222222222222222222"}]}'
    )

    actual = canonical_part_plan_bytes(_compact_plan())

    assert actual == expected
    assert not actual.endswith(b"\n")
    assert part_plan_sha256(_compact_plan()).value == hashlib.sha256(expected).hexdigest()
    assert parse_canonical_part_plan(expected) == _compact_plan()


def test_part_plan_build_is_independent_of_in_memory_part_order() -> None:
    plan = _compact_plan()
    reversed_plan = PartPlan.build(
        schema_version=plan.schema_version,
        blob_size_bytes=plan.blob_size_bytes,
        part_size_bytes=plan.part_size_bytes,
        parts=reversed(plan.parts),
    )

    assert canonical_part_plan_bytes(reversed_plan) == canonical_part_plan_bytes(plan)
    assert part_plan_sha256(reversed_plan) == part_plan_sha256(plan)


def test_hashed_part_plan_rejects_a_digest_for_different_canonical_bytes() -> None:
    plan = _compact_plan()

    assert HashedMultipartPlan.from_plan(plan).sha256 == part_plan_sha256(plan)
    with pytest.raises(ContentConflictError):
        HashedMultipartPlan(plan, Sha256Digest("00" * 32))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: replace(plan, schema_version=2),
        lambda plan: replace(plan, blob_size_bytes=plan.blob_size_bytes + 1),
        lambda plan: replace(plan, part_size_bytes=plan.part_size_bytes + 1),
        lambda plan: replace(
            plan,
            parts=(
                replace(plan.parts[0], size_bytes=plan.parts[0].size_bytes + 1),
                replace(
                    plan.parts[1],
                    offset_bytes=plan.parts[1].offset_bytes + 1,
                    size_bytes=plan.parts[1].size_bytes - 1,
                ),
            ),
        ),
        lambda plan: replace(
            plan,
            parts=(replace(plan.parts[0], expected_sha256=Sha256Digest("3" * 64)), plan.parts[1]),
        ),
    ],
)
def test_every_part_plan_identity_field_changes_or_invalidates_hash(mutation: object) -> None:
    plan = _compact_plan()
    mutate = mutation

    try:
        changed = mutate(plan)  # type: ignore[operator]
    except (ManifestMismatchError, ValueError):
        return

    assert part_plan_sha256(changed) != part_plan_sha256(plan)


@pytest.mark.parametrize(
    "data",
    [
        canonical_part_plan_bytes(_compact_plan()) + b"\n",
        canonical_part_plan_bytes(_compact_plan()).replace(
            b'"schema_version":1', b'"schema_version": 1'
        ),
        canonical_part_plan_bytes(_compact_plan()).replace(
            b'"schema_version":1', b'"schema_version":1.0'
        ),
        canonical_part_plan_bytes(_compact_plan()).replace(b'"part_count":2', b'"part_count":true'),
        canonical_part_plan_bytes(_compact_plan()).replace(b'"parts"', b'"unknown":0,"parts"'),
        canonical_part_plan_bytes(_compact_plan()).replace(b'"sha256":"1', b'"sha256":"A', 1),
        canonical_part_plan_bytes(_compact_plan()).replace(
            b'{"schema_version":1', b'{"schema_version":1,"schema_version":1', 1
        ),
    ],
)
def test_part_plan_parser_rejects_noncanonical_or_invalid_json(data: bytes) -> None:
    with pytest.raises(ManifestMismatchError):
        parse_canonical_part_plan(data)


def test_typed_identities_do_not_alias_each_other() -> None:
    value = uuid4()
    session_identity: object = MultipartSessionId(value)
    request_identity: object = MultipartRequestId(value)

    assert session_identity != request_identity
    assert MultipartInvocationId(value).value == value
    assert AdmissionLeaseOwnerId(value).value == value
    assert CompletionLeaseOwnerId(value).value == value
    assert MultipartSessionGeneration(1).value == 1
    assert AdmissionLeaseEpoch(1).value == 1
    assert CompletionLeaseEpoch(1).value == 1

    for identity in (
        MultipartSessionGeneration,
        AdmissionLeaseEpoch,
        CompletionLeaseEpoch,
    ):
        with pytest.raises(ValueError):
            identity(0)


def test_every_documented_multipart_session_edge_is_accepted() -> None:
    legal_edges = {
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

    for current in MultipartSessionState:
        for target in MultipartSessionState:
            if current == target or (current, target) in legal_edges:
                assert require_multipart_session_transition(current, target) is target
            elif (
                current is MultipartSessionState.COMPLETING
                and target is MultipartSessionState.IN_PROGRESS
            ):
                with pytest.raises(IllegalTransitionError):
                    require_multipart_session_transition(current, target)
                assert (
                    require_multipart_session_transition(
                        current, target, guarded_completion_recovery=True
                    )
                    is target
                )
            else:
                with pytest.raises(IllegalTransitionError):
                    require_multipart_session_transition(current, target)


def test_upload_part_state_has_no_issued_or_failed_state_and_resets_are_guarded() -> None:
    assert {state.value for state in UploadPartState} == {"PENDING", "UPLOADED", "VERIFIED"}
    assert (
        require_upload_part_transition(UploadPartState.PENDING, UploadPartState.UPLOADED)
        is UploadPartState.UPLOADED
    )
    assert (
        require_upload_part_transition(UploadPartState.UPLOADED, UploadPartState.VERIFIED)
        is UploadPartState.VERIFIED
    )

    for current in (UploadPartState.UPLOADED, UploadPartState.VERIFIED):
        with pytest.raises(IllegalTransitionError):
            require_upload_part_transition(current, UploadPartState.PENDING)
        assert (
            require_upload_part_transition(
                current, UploadPartState.PENDING, provider_reconciled=True
            )
            is UploadPartState.PENDING
        )


def test_completion_phase_tracks_long_work_without_blob_state() -> None:
    assert require_completion_phase_transition(CompletionPhase.PENDING, CompletionPhase.ASSEMBLING)
    assert require_completion_phase_transition(
        CompletionPhase.PENDING, CompletionPhase.FINAL_PRESENT
    )
    assert require_completion_phase_transition(
        CompletionPhase.ASSEMBLING, CompletionPhase.FINAL_PRESENT
    )
    assert require_completion_phase_transition(
        CompletionPhase.FINAL_PRESENT, CompletionPhase.FINAL_VERIFICATION
    )
    assert require_completion_phase_transition(
        CompletionPhase.FINAL_VERIFICATION, CompletionPhase.FINAL_PRESENT
    )
    with pytest.raises(IllegalTransitionError):
        require_completion_phase_transition(
            CompletionPhase.FINAL_VERIFICATION, CompletionPhase.ASSEMBLING
        )


def test_completed_part_receipt_normalizes_provider_sha256() -> None:
    expected = Sha256Digest("00" * 32)
    receipt = CompletedPartReceipt.from_upload_response(
        part_number=1,
        response_etag='"opaque"',
        response_checksum_sha256_base64=sha256_digest_to_base64(expected),
        expected_sha256=expected,
    )

    assert receipt.response_checksum_sha256_base64 == "A" * 43 + "="
    assert sha256_base64_to_digest(receipt.response_checksum_sha256_base64) == expected
    assert receipt.validate_for(expected) is receipt


@pytest.mark.parametrize("checksum", ["", "not-base64", "AA==", "A" * 44])
def test_completed_part_receipt_rejects_invalid_provider_checksum(checksum: str) -> None:
    with pytest.raises(PartChecksumRejectedError):
        CompletedPartReceipt(1, '"opaque"', checksum)


def test_completed_part_receipt_rejects_mismatch_and_missing_etag() -> None:
    zero = Sha256Digest("00" * 32)
    one = Sha256Digest("01" * 32)

    with pytest.raises(PartChecksumRejectedError):
        CompletedPartReceipt.from_upload_response(1, '"opaque"', zero.checksum_base64, one)
    with pytest.raises(ContentConflictError):
        CompletedPartReceipt(1, "", zero.checksum_base64)


def test_identical_receipts_for_different_part_numbers_are_valid() -> None:
    checksum = Sha256Digest("ab" * 32).checksum_base64

    first = CompletedPartReceipt(1, '"same"', checksum)
    second = CompletedPartReceipt(2, '"same"', checksum)

    assert first.response_etag == second.response_etag
    assert first.response_checksum_sha256_base64 == second.response_checksum_sha256_base64


def test_verification_evidence_is_structural_application_evidence() -> None:
    digest = Sha256Digest("ab" * 32)
    evidence = VerificationEvidence(
        verification_method=VerificationMethod.FULL_STREAM_SHA256,
        observed_sha256=digest,
        observed_size_bytes=123,
        verification_read_bytes=123,
        verification_completed_at=datetime.now(UTC),
        verifier_version="robolake-test/1",
    )

    assert evidence.validate_for(digest, 123) is evidence
    with pytest.raises(ContentConflictError):
        evidence.validate_for(Sha256Digest("cd" * 32), 123)
    with pytest.raises(ContentConflictError):
        replace(evidence, verification_read_bytes=122).validate_for(digest, 123)


def test_partial_completion_evidence_rejects_409_and_naive_times() -> None:
    now = datetime.now(UTC)
    observation = ProviderPartObservation(
        part_number=1,
        size_bytes=1,
        etag='"opaque"',
        checksum_sha256_base64=Sha256Digest("00" * 32).checksum_base64,
    )

    assert PartialCompletionEvidence(now, now, (observation,)).provider_parts == (observation,)
    with pytest.raises(ContentConflictError):
        PartialCompletionEvidence(now, now, (), completion_result="CONFLICT_409")
    with pytest.raises(ContentConflictError):
        PartialCompletionEvidence(datetime.now(), now, ())


def test_lease_records_require_aware_time_and_monotonic_epochs() -> None:
    now = datetime.now(UTC)
    session_id = MultipartSessionId(uuid4())
    admission = AdmissionLeaseRecord(
        session_id=session_id,
        invocation_id=MultipartInvocationId(uuid4()),
        lease_owner_id=AdmissionLeaseOwnerId(uuid4()),
        lease_epoch=AdmissionLeaseEpoch(1),
        expires_at=now,
        last_renewed_at=now,
    )
    completion = CompletionLeaseRecord(
        session_id=session_id,
        lease_owner_id=CompletionLeaseOwnerId(uuid4()),
        lease_epoch=CompletionLeaseEpoch(1),
        expires_at=now,
        last_heartbeat_at=now,
    )

    assert admission.lease_epoch.value == completion.lease_epoch.value == 1
    with pytest.raises(ContentConflictError):
        replace(admission, expires_at=datetime.now())


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (LocalFileChangedError, "LOCAL_FILE_CHANGED"),
        (InvalidPartNumberError, "INVALID_PART_NUMBER"),
        (PartSizeMismatchError, "PART_SIZE_MISMATCH"),
        (PartChecksumRejectedError, "PART_CHECKSUM_REJECTED"),
        (MultipartSessionNotFoundError, "MULTIPART_SESSION_NOT_FOUND"),
        (AdmissionLeaseHeldError, "ADMISSION_LEASE_HELD"),
        (AdmissionCapacityExhaustedError, "ADMISSION_CAPACITY_EXHAUSTED"),
        (AdmissionLeaseLostError, "ADMISSION_LEASE_LOST"),
        (MultipartInitiationInProgressError, "MULTIPART_INITIATION_IN_PROGRESS"),
        (MultipartInitiationAmbiguousError, "MULTIPART_INITIATION_AMBIGUOUS"),
        (MultipartCompletionAmbiguousError, "MULTIPART_COMPLETION_AMBIGUOUS"),
        (FinalBlobPublicationConflictError, "FINAL_BLOB_PUBLICATION_CONFLICT"),
    ],
)
def test_m2_error_codes_are_stable(error_type: type[Exception], code: str) -> None:
    assert error_type.code == code  # type: ignore[attr-defined]
