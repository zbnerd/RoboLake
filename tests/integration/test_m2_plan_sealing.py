"""One-time PostgreSQL sealing for deterministic multipart PartPlans."""

from __future__ import annotations

from time import perf_counter

import pytest
from robolake.domain.constants import BASE_PART_BYTES, MAX_MULTIPART_BLOB_BYTES
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError
from tests.integration.m2_helpers import build_multipart_plan, insert_multipart_session

pytestmark = pytest.mark.integration


def test_invalid_created_plan_is_not_scanned_on_unrelated_session_updates(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(
        database_connection,
        offset_delta_at_part=(2, 1),
    )

    database_connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    database_connection.execute(
        text("UPDATE upload_sessions SET last_activity_at = clock_timestamp() WHERE id = :id"),
        {"id": context.session_id},
    )
    database_connection.execute(
        text(
            "INSERT INTO multipart_admission_leases "
            "(upload_session_id, invocation_id, owner_id, epoch, acquired_at, renewed_at, "
            "expires_at) VALUES (:id, gen_random_uuid(), gen_random_uuid(), 1, "
            "clock_timestamp(), clock_timestamp(), clock_timestamp() + interval '60 seconds')"
        ),
        {"id": context.session_id},
    )
    database_connection.execute(
        text(
            "UPDATE multipart_admission_leases SET renewed_at = clock_timestamp(), "
            "expires_at = clock_timestamp() + interval '120 seconds' "
            "WHERE upload_session_id = :id"
        ),
        {"id": context.session_id},
    )
    database_connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "UPDATE upload_sessions SET state = 'INITIATING', "
                "initiation_started_at = clock_timestamp() WHERE id = :id"
            ),
            {"id": context.session_id},
        )


@pytest.mark.parametrize(
    "blob_size_bytes",
    [
        MAX_MULTIPART_BLOB_BYTES,
        BASE_PART_BYTES * 10_000,
    ],
    ids=["protocol-maximum-9314-parts", "exactly-10000-parts"],
)
def test_large_deterministic_plan_seals_once_within_statement_budget(
    database_connection: Connection,
    blob_size_bytes: int,
) -> None:
    database_connection.execute(text("SET LOCAL statement_timeout = '30s'"))
    plan = build_multipart_plan(blob_size_bytes)
    started = perf_counter()
    context = insert_multipart_session(database_connection, plan=plan)
    database_connection.execute(
        text(
            "UPDATE upload_sessions SET state = 'INITIATING', "
            "initiation_started_at = clock_timestamp() WHERE id = :id"
        ),
        {"id": context.session_id},
    )
    elapsed = perf_counter() - started

    assert plan.part_count in {9_314, 10_000}
    assert elapsed < 30


def test_part_plan_fields_are_immutable_after_seal(database_connection: Connection) -> None:
    context = insert_multipart_session(database_connection)
    database_connection.execute(
        text(
            "UPDATE upload_sessions SET state = 'INITIATING', "
            "initiation_started_at = clock_timestamp() WHERE id = :id"
        ),
        {"id": context.session_id},
    )

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "UPDATE upload_parts SET offset_bytes = offset_bytes + 1 "
                "WHERE upload_session_id = :id AND part_number = 1"
            ),
            {"id": context.session_id},
        )
