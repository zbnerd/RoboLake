"""Upload admission lease fencing and capacity contracts."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from robolake.domain.errors import AdmissionLeaseHeldError, AdmissionLeaseLostError
from robolake.domain.identifiers import Sha256Digest
from robolake.domain.multipart import CompletedPartReceipt, PartAttribution
from robolake.domain.records import ProviderPartObservation
from robolake.infrastructure.database import create_session_factory
from robolake.infrastructure.store import SqlAlchemyStore
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from tests.integration.m2_helpers import MultipartDatabaseContext, insert_multipart_session

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


def _new_session(engine: Engine) -> MultipartDatabaseContext:
    with engine.begin() as connection:
        return insert_multipart_session(connection)


def _expire_admission(engine: Engine, session_id: object) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE multipart_admission_leases SET renewed_at = stamp.value, "
                "expires_at = stamp.value FROM (SELECT clock_timestamp() AS value) stamp "
                "WHERE upload_session_id = :session_id"
            ),
            {"session_id": session_id},
        )


def test_acquire_replay_renew_takeover_and_stale_fence(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _new_session(engine)
    invocation = uuid4()
    request_id = uuid4()

    first = store.acquire_multipart_lease(request_id, invocation, context.session_id, 60, 64)
    replay = store.acquire_multipart_lease(request_id, invocation, context.session_id, 60, 64)
    renewed = store.acquire_multipart_lease(uuid4(), invocation, context.session_id, 120, 64)

    assert replay == first
    assert renewed.lease_owner_id == first.lease_owner_id
    assert renewed.lease_epoch == first.lease_epoch
    assert renewed.expires_at > first.expires_at
    with pytest.raises(AdmissionLeaseHeldError):
        store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)

    _expire_admission(engine, context.session_id)
    takeover = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    assert takeover.lease_epoch.value == first.lease_epoch.value + 1
    assert takeover.lease_owner_id != first.lease_owner_id
    with pytest.raises(AdmissionLeaseLostError):
        store.release_multipart_lease(
            context.session_id, first.lease_owner_id.value, first.lease_epoch.value
        )
    store.release_multipart_lease(
        context.session_id, takeover.lease_owner_id.value, takeover.lease_epoch.value
    )


def test_direct_sql_cannot_rewind_or_steal_admission_epoch(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _new_session(engine)
    lease = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE multipart_admission_leases SET owner_id = :owner, "
                "epoch = epoch + 1 WHERE upload_session_id = :id"
            ),
            {"id": context.session_id, "owner": uuid4()},
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE multipart_admission_leases SET epoch = epoch - 1 "
                "WHERE upload_session_id = :id"
            ),
            {"id": context.session_id},
        )
    store.release_multipart_lease(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value
    )


def test_initiation_ambiguity_terminalization_releases_capacity_atomically(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _new_session(engine)
    lease = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 1)
    store.begin_provider_initiation(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value
    )

    store.mark_multipart_initiation_ambiguous(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value
    )

    with engine.connect() as connection:
        state, failure_code, is_released = connection.execute(
            text(
                "SELECT session.state, session.failure_code, "
                "lease.expires_at <= clock_timestamp() FROM upload_sessions session "
                "JOIN multipart_admission_leases lease ON lease.upload_session_id = session.id "
                "WHERE session.id = :id"
            ),
            {"id": context.session_id},
        ).one()
    assert (state, failure_code, is_released) == ("FAILED", "INITIATION_AMBIGUOUS", True)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("UPDATE upload_sessions SET state = 'IN_PROGRESS' WHERE id = :id"),
            {"id": context.session_id},
        )

    next_context = _new_session(engine)
    next_lease = store.acquire_multipart_lease(uuid4(), uuid4(), next_context.session_id, 60, 1)
    assert next_lease.lease_epoch.value == 1
    store.release_multipart_lease(
        next_context.session_id,
        next_lease.lease_owner_id.value,
        next_lease.lease_epoch.value,
    )


def test_provider_upload_id_is_durably_recorded_once(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _new_session(engine)
    lease = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.begin_provider_initiation(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value
    )

    store.record_provider_upload(
        context.session_id,
        lease.lease_owner_id.value,
        lease.lease_epoch.value,
        "opaque-provider-upload-id",
    )

    persisted = store.get_multipart(context.session_id)
    assert persisted.session.state.value == "IN_PROGRESS"
    assert persisted.session.provider_upload_id == "opaque-provider-upload-id"
    assert persisted.blob.state.value == "UPLOADING"
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("UPDATE upload_sessions SET provider_upload_id = 'retargeted' WHERE id = :id"),
            {"id": context.session_id},
        )
    store.release_multipart_lease(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value
    )


def test_list_parts_reconciliation_requires_a_successful_upload_receipt(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _new_session(engine)
    lease = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.begin_provider_initiation(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value
    )
    store.record_provider_upload(
        context.session_id,
        lease.lease_owner_id.value,
        lease.lease_epoch.value,
        "provider-reconcile-test",
    )
    first = context.plan.parts[0]
    second = context.plan.parts[1]
    receipt = CompletedPartReceipt.from_upload_response(
        first.part_number,
        '"part-one"',
        first.expected_sha256.checksum_base64,
        first.expected_sha256,
    )
    store.record_uploaded_part(
        context.session_id,
        lease.lease_owner_id.value,
        lease.lease_epoch.value,
        receipt,
    )

    result = store.reconcile_parts(
        context.session_id,
        lease.lease_owner_id.value,
        lease.lease_epoch.value,
        (
            ProviderPartObservation(
                first.part_number,
                first.size_bytes,
                receipt.response_etag,
                receipt.response_checksum_sha256_base64,
            ),
            ProviderPartObservation(
                second.part_number,
                second.size_bytes,
                '"list-only"',
                second.expected_sha256.checksum_base64,
            ),
        ),
        PartAttribution.RECONCILED,
    )

    assert result.resolved_part_count == 1
    persisted = store.get_multipart(context.session_id)
    assert persisted.parts[0].state.value == "VERIFIED"
    assert persisted.parts[1].state.value == "PENDING"
    assert persisted.parts[1].response_receipt is None
    store.release_multipart_lease(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value
    )


def test_mismatching_provider_checksum_resets_part_without_persisting_false_evidence(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _new_session(engine)
    lease = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.begin_provider_initiation(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value
    )
    store.record_provider_upload(
        context.session_id,
        lease.lease_owner_id.value,
        lease.lease_epoch.value,
        "provider-mismatch-test",
    )
    first = context.plan.parts[0]
    receipt = CompletedPartReceipt.from_upload_response(
        first.part_number,
        '"part-one"',
        first.expected_sha256.checksum_base64,
        first.expected_sha256,
    )
    store.record_uploaded_part(
        context.session_id,
        lease.lease_owner_id.value,
        lease.lease_epoch.value,
        receipt,
    )

    result = store.reconcile_parts(
        context.session_id,
        lease.lease_owner_id.value,
        lease.lease_epoch.value,
        (
            ProviderPartObservation(
                first.part_number,
                first.size_bytes,
                receipt.response_etag,
                Sha256Digest("11" * 32).checksum_base64,
            ),
        ),
        PartAttribution.RECONCILED,
    )

    assert result.resolved_part_count == 0
    persisted = store.get_multipart(context.session_id)
    assert persisted.parts[0].state.value == "PENDING"
    with engine.connect() as connection:
        listed_checksum, error_code = connection.execute(
            text(
                "SELECT listed_checksum_sha256_base64, last_error_code "
                "FROM upload_parts WHERE upload_session_id = :session_id "
                "AND part_number = :part_number"
            ),
            {"session_id": context.session_id, "part_number": first.part_number},
        ).one()
    assert listed_checksum is None
    assert error_code == "PART_CHECKSUM_REJECTED"
    store.release_multipart_lease(
        context.session_id, lease.lease_owner_id.value, lease.lease_epoch.value
    )


def test_stale_owner_cannot_terminalize_or_release_current_owner(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _new_session(engine)
    first = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
    store.begin_provider_initiation(
        context.session_id, first.lease_owner_id.value, first.lease_epoch.value
    )
    _expire_admission(engine, context.session_id)
    second = store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)

    with pytest.raises(AdmissionLeaseLostError):
        store.mark_multipart_initiation_ambiguous(
            context.session_id, first.lease_owner_id.value, first.lease_epoch.value
        )
    store.mark_multipart_initiation_ambiguous(
        context.session_id, second.lease_owner_id.value, second.lease_epoch.value
    )


def test_sixty_four_abandoned_sessions_do_not_permanently_consume_capacity(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    for _ in range(64):
        context = _new_session(engine)
        store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE multipart_admission_leases SET renewed_at = stamp.value, "
                "expires_at = stamp.value FROM (SELECT clock_timestamp() AS value) stamp"
            )
        )
    next_context = _new_session(engine)
    acquired = store.acquire_multipart_lease(uuid4(), uuid4(), next_context.session_id, 60, 64)
    assert acquired.lease_epoch.value == 1
    store.release_multipart_lease(
        next_context.session_id,
        acquired.lease_owner_id.value,
        acquired.lease_epoch.value,
    )


def test_two_invocations_racing_for_one_session_have_one_fenced_winner(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _new_session(engine)
    barrier = Barrier(2)

    def acquire() -> object:
        barrier.wait()
        try:
            return store.acquire_multipart_lease(uuid4(), uuid4(), context.session_id, 60, 64)
        except AdmissionLeaseHeldError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(acquire), executor.submit(acquire)]
        outcomes = [future.result() for future in results]

    winners = [item for item in outcomes if not isinstance(item, AdmissionLeaseHeldError)]
    losers = [item for item in outcomes if isinstance(item, AdmissionLeaseHeldError)]
    assert len(winners) == len(losers) == 1
