"""Server-owned completion acceptance and lease fencing contracts."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from robolake.domain.errors import (
    AdmissionLeaseLostError,
    IdempotencyConflictError,
    IllegalTransitionError,
)
from robolake.domain.multipart import (
    CompletionPhase,
    CompletionRecoveryAction,
    CompletionRecoveryReason,
    VerificationMethod,
)
from robolake.domain.records import (
    HashedMultipartPlan,
    PartialCompletionEvidence,
    ProviderPartObservation,
    VerificationEvidence,
)
from robolake.infrastructure.database import create_session_factory
from robolake.infrastructure.store import SqlAlchemyStore
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from tests.integration.m2_helpers import (
    MultipartDatabaseContext,
    insert_multipart_session,
    move_session_to_in_progress,
    verify_all_parts,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def store_and_engine(
    isolated_migrated_database_url: str,
) -> Iterator[tuple[SqlAlchemyStore, Engine]]:
    engine = create_engine(isolated_migrated_database_url)
    try:
        yield SqlAlchemyStore(create_session_factory(engine)), engine
    finally:
        engine.dispose()


def _completion_ready_session(engine: Engine) -> MultipartDatabaseContext:
    with engine.begin() as connection:
        context = insert_multipart_session(connection)
        move_session_to_in_progress(connection, context)
        verify_all_parts(connection, context)
        return context


def _terminalize_test_work(engine: Engine, *session_ids: UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE upload_sessions SET state = 'FAILED', "
                "failure_code = 'PLAN_INVARIANT' "
                "WHERE id = ANY(:session_ids) AND state = 'COMPLETING'"
            ),
            {"session_ids": list(session_ids)},
        )
        connection.execute(
            text(
                "UPDATE multipart_completion_leases SET renewed_at = stamp.value, "
                "expires_at = stamp.value FROM (SELECT clock_timestamp() AS value) stamp "
                "WHERE upload_session_id = ANY(:session_ids)"
            ),
            {"session_ids": list(session_ids)},
        )


def test_completion_acceptance_replays_after_admission_release(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    lease = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    request_id = uuid4()

    accepted = store.accept_completion(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value, request_id
    )
    replayed = store.accept_completion(
        context.session_id, uuid4(), lease.lease_epoch.value + 100, request_id
    )

    assert replayed == accepted
    with engine.connect() as connection:
        state, phase, lease_released = connection.execute(
            text(
                "SELECT session.state, session.completion_phase, "
                "lease.expires_at <= clock_timestamp() FROM upload_sessions session "
                "JOIN multipart_admission_leases lease ON lease.upload_session_id = session.id "
                "WHERE session.id = :id"
            ),
            {"id": context.session_id},
        ).one()
    assert (state, phase, lease_released) == ("COMPLETING", "PENDING", True)
    _terminalize_test_work(engine, context.session_id)


def test_concurrent_same_completion_request_converges_on_stored_202(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    lease = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    request_id = uuid4()
    barrier = Barrier(2)

    def accept() -> object:
        barrier.wait()
        return store.accept_completion(
            context.session_id,
            lease.lease_owner_id.value,
            lease.lease_epoch.value,
            request_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(accept) for _ in range(2)]
        first, second = (future.result() for future in futures)

    assert first == second
    _terminalize_test_work(engine, context.session_id)


def test_completion_request_id_cannot_be_retargeted(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    first = _completion_ready_session(engine)
    first_lease = store.acquire_multipart_lease(uuid4(), uuid4(), first.session_id, 60, 64)
    request_id = uuid4()
    store.accept_completion(
        first.session_id,
        first_lease.lease_owner_id.value,
        first_lease.lease_epoch.value,
        request_id,
    )
    second = _completion_ready_session(engine)
    second_lease = store.acquire_multipart_lease(uuid4(), uuid4(), second.session_id, 60, 64)

    with pytest.raises(IdempotencyConflictError):
        store.accept_completion(
            second.session_id,
            second_lease.lease_owner_id.value,
            second_lease.lease_epoch.value,
            request_id,
        )
    _terminalize_test_work(engine, first.session_id, second.session_id)


def test_completion_claim_is_separate_fenced_and_reclaimable(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    admission = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.accept_completion(
        context.session_id,
        admission.lease_owner_id.value,
        admission.lease_epoch.value,
        uuid4(),
    )
    first_owner = uuid4()
    first = store.claim_completion(first_owner, 60, 1)
    assert first is not None
    assert store.claim_completion(uuid4(), 60, 1) is None
    store.set_completion_phase(first, CompletionPhase.ASSEMBLING)
    heartbeat = store.renew_completion_lease(
        context.session_id, first_owner, first.lease.lease_epoch.value, 120
    )
    assert heartbeat.expires_at > first.lease.expires_at

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE multipart_completion_leases SET renewed_at = stamp.value, "
                "expires_at = stamp.value FROM (SELECT clock_timestamp() AS value) stamp "
                "WHERE upload_session_id = :id"
            ),
            {"id": context.session_id},
        )
    second = store.claim_completion(uuid4(), 60, 1)
    assert second is not None
    assert second.lease.lease_epoch.value == first.lease.lease_epoch.value + 1
    with pytest.raises(AdmissionLeaseLostError):
        store.set_completion_phase(first, CompletionPhase.FINAL_PRESENT)
    store.set_completion_phase(second, CompletionPhase.FINAL_PRESENT)
    _terminalize_test_work(engine, context.session_id)


def test_expired_completion_lease_can_be_reclaimed_by_same_owner(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    admission = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.accept_completion(
        context.session_id,
        admission.lease_owner_id.value,
        admission.lease_epoch.value,
        uuid4(),
    )
    owner = uuid4()
    first = store.claim_completion(owner, 60, 1)
    assert first is not None
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE multipart_completion_leases SET renewed_at = stamp.value, "
                "expires_at = stamp.value FROM (SELECT clock_timestamp() AS value) stamp "
                "WHERE upload_session_id = :id"
            ),
            {"id": context.session_id},
        )

    second = store.claim_completion(owner, 60, 1)

    assert second is not None
    assert second.lease.lease_owner_id == first.lease.lease_owner_id
    assert second.lease.lease_epoch.value == first.lease.lease_epoch.value + 1
    with pytest.raises(AdmissionLeaseLostError):
        store.set_completion_phase(first, CompletionPhase.ASSEMBLING)
    store.set_completion_phase(second, CompletionPhase.ASSEMBLING)
    _terminalize_test_work(engine, context.session_id)


def test_verified_completion_commits_evidence_blob_and_session_under_one_fence(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    admission = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.accept_completion(
        context.session_id,
        admission.lease_owner_id.value,
        admission.lease_epoch.value,
        uuid4(),
    )
    claim = store.claim_completion(uuid4(), 60, 2)
    assert claim is not None
    registered = store.get_multipart(context.session_id)
    evidence = VerificationEvidence(
        verification_method=VerificationMethod.FULL_STREAM_SHA256,
        observed_sha256=registered.blob.sha256,
        observed_size_bytes=registered.blob.size_bytes,
        verification_read_bytes=registered.blob.size_bytes,
        verification_completed_at=datetime.now(UTC),
        verifier_version="robolake-test/1",
    )

    with pytest.raises(IllegalTransitionError):
        store.record_verified_completion(claim, evidence)
    store.set_completion_phase(claim, CompletionPhase.ASSEMBLING)
    store.set_completion_phase(claim, CompletionPhase.FINAL_PRESENT)
    store.set_completion_phase(claim, CompletionPhase.FINAL_VERIFICATION)

    completed = store.record_verified_completion(claim, evidence)

    assert completed.session.state.value == "COMPLETED"
    assert completed.blob.state.value == "AVAILABLE"
    with pytest.raises(IllegalTransitionError):
        store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT expires_at <= clock_timestamp() "
                "FROM multipart_completion_leases WHERE upload_session_id = :id"
            ),
            {"id": context.session_id},
        ).scalar_one()


def test_guarded_partial_completion_reuses_only_receipt_backed_provider_parts(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    admission = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.accept_completion(
        context.session_id,
        admission.lease_owner_id.value,
        admission.lease_epoch.value,
        uuid4(),
    )
    claim = store.claim_completion(uuid4(), 60, 1)
    assert claim is not None
    registered = store.get_multipart(context.session_id)
    first = registered.parts[0]
    assert first.response_receipt is not None
    observed_at = datetime.now(UTC)
    evidence = PartialCompletionEvidence(
        recovery_reason=CompletionRecoveryReason.PARTS_PARTIAL,
        final_absence_observed_at=observed_at,
        provider_reconciled_at=observed_at,
        provider_parts=(
            ProviderPartObservation(
                part_number=first.definition.part_number,
                size_bytes=first.definition.size_bytes,
                etag=first.response_receipt.response_etag,
                checksum_sha256_base64=(first.response_receipt.response_checksum_sha256_base64),
            ),
        ),
    )

    result = store.recover_partial_completion(claim, evidence)
    recovered = result.context

    assert result.action is CompletionRecoveryAction.RESUME_PART_UPLOAD
    assert recovered.session.state.value == "IN_PROGRESS"
    assert recovered.session.completion_phase is None
    assert recovered.parts[0].state.value == "VERIFIED"
    assert all(part.state.value == "PENDING" for part in recovered.parts[1:])
    assert recovered.blob.state.value == "UPLOADING"


def test_all_matching_parts_keep_completing_and_retry_complete(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    admission = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.accept_completion(
        context.session_id,
        admission.lease_owner_id.value,
        admission.lease_epoch.value,
        uuid4(),
    )
    claim = store.claim_completion(uuid4(), 60, 1)
    assert claim is not None
    registered = store.get_multipart(context.session_id)
    observed_at = datetime.now(UTC)
    observations = tuple(
        ProviderPartObservation(
            part.definition.part_number,
            part.definition.size_bytes,
            part.response_receipt.response_etag,
            part.response_receipt.response_checksum_sha256_base64,
        )
        for part in registered.parts
        if part.response_receipt is not None
    )
    evidence = PartialCompletionEvidence(
        recovery_reason=CompletionRecoveryReason.PARTS_READY,
        final_absence_observed_at=observed_at,
        provider_reconciled_at=observed_at,
        provider_parts=observations,
    )

    result = store.recover_partial_completion(claim, evidence)

    assert result.action is CompletionRecoveryAction.RETRY_COMPLETION
    assert result.context.session.state.value == "COMPLETING"
    assert result.context.session.completion_phase is CompletionPhase.PENDING
    assert all(part.state.value == "VERIFIED" for part in result.context.parts)
    _terminalize_test_work(engine, context.session_id)


@pytest.mark.parametrize(
    "reason",
    [
        CompletionRecoveryReason.CONDITIONAL_409,
        CompletionRecoveryReason.MULTIPART_SESSION_NOT_FOUND,
    ],
)
def test_invalidated_provider_attempt_requires_a_new_generation(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
    reason: CompletionRecoveryReason,
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    admission = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.accept_completion(
        context.session_id,
        admission.lease_owner_id.value,
        admission.lease_epoch.value,
        uuid4(),
    )
    claim = store.claim_completion(uuid4(), 60, 1)
    assert claim is not None
    observed_at = datetime.now(UTC)
    evidence = PartialCompletionEvidence(
        recovery_reason=reason,
        final_absence_observed_at=observed_at,
        provider_reconciled_at=observed_at,
        provider_parts=(),
    )

    result = store.recover_partial_completion(claim, evidence)

    assert result.action is CompletionRecoveryAction.START_NEW_GENERATION
    assert result.context.session.state.value == "FAILED"
    assert result.context.session.failure_code == "PROVIDER_ATTEMPT_INVALIDATED"
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT expires_at <= clock_timestamp() "
                "FROM multipart_completion_leases WHERE upload_session_id = :id"
            ),
            {"id": context.session_id},
        ).scalar_one()
    next_generation = store.create_or_resolve_multipart(
        uuid4(),
        context.version_id,
        context.blob_id,
        HashedMultipartPlan.from_plan(context.plan),
    )
    assert next_generation.session.generation.value == 2
    assert all(part.state.value == "PENDING" for part in next_generation.parts)


def test_concurrent_completion_claims_have_one_winner(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    admission = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.accept_completion(
        context.session_id,
        admission.lease_owner_id.value,
        admission.lease_epoch.value,
        uuid4(),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(store.claim_completion, uuid4(), 60, 1),
            executor.submit(store.claim_completion, uuid4(), 60, 1),
        ]
        claims = [future.result() for future in futures]

    assert sum(claim is not None for claim in claims) == 1
    _terminalize_test_work(engine, context.session_id)


def test_direct_sql_cannot_steal_unexpired_completion_epoch(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _completion_ready_session(engine)
    admission = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.accept_completion(
        context.session_id,
        admission.lease_owner_id.value,
        admission.lease_epoch.value,
        uuid4(),
    )
    claim = store.claim_completion(uuid4(), 60, 1)
    assert claim is not None

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE multipart_completion_leases SET owner_instance_id = :owner, "
                "epoch = epoch + 1 WHERE upload_session_id = :id"
            ),
            {"id": context.session_id, "owner": uuid4()},
        )
    _terminalize_test_work(engine, context.session_id)
