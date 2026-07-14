"""Direct-SQL proof of M2 multipart structural invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from robolake.domain.identifiers import Sha256Digest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError
from tests.integration.m2_helpers import (
    insert_multipart_session,
    move_session_to_in_progress,
    verify_all_parts,
)

pytestmark = pytest.mark.integration


def _force_deferred_constraints(connection: Connection) -> None:
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def test_valid_complete_created_plan_satisfies_deferred_constraints(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)

    _force_deferred_constraints(database_connection)

    assert (
        database_connection.execute(
            text("SELECT count(*) FROM upload_parts WHERE upload_session_id = :id"),
            {"id": context.session_id},
        ).scalar_one()
        == context.plan.part_count
    )


@pytest.mark.parametrize("delta", [-1, 1])
def test_overlapping_or_gapped_initial_part_plan_is_rejected(
    database_connection: Connection, delta: int
) -> None:
    insert_multipart_session(
        database_connection,
        offset_delta_at_part=(2, delta),
    )

    with pytest.raises(IntegrityError):
        _force_deferred_constraints(database_connection)


@pytest.mark.parametrize(
    "assignment",
    [
        "session_generation = session_generation + 1",
        "part_size_bytes = part_size_bytes * 2",
        "planned_part_count = planned_part_count - 1",
        "part_plan_sha256 = repeat('f', 64)",
        "blob_id = gen_random_uuid()",
    ],
)
def test_multipart_session_identity_and_plan_are_immutable(
    database_connection: Connection, assignment: str
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(f"UPDATE upload_sessions SET {assignment} WHERE id = :id"),
            {"id": context.session_id},
        )


@pytest.mark.parametrize(
    "assignment",
    [
        "part_number = part_number + 100",
        "offset_bytes = offset_bytes + 1",
        "size_bytes = size_bytes - 1",
        "sha256 = repeat('f', 64)",
        "upload_session_id = gen_random_uuid()",
    ],
)
def test_part_identity_and_boundaries_are_immutable(
    database_connection: Connection, assignment: str
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                f"UPDATE upload_parts SET {assignment} "
                "WHERE upload_session_id = :id AND part_number = 1"
            ),
            {"id": context.session_id},
        )


def test_duplicate_part_number_and_offset_are_rejected(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "INSERT INTO upload_parts "
                "(upload_session_id, part_number, offset_bytes, size_bytes, sha256, state, "
                "capability_issue_count, created_at) SELECT upload_session_id, part_number, "
                "offset_bytes, size_bytes, sha256, state, 0, :now FROM upload_parts "
                "WHERE upload_session_id = :id AND part_number = 1"
            ),
            {"id": context.session_id, "now": datetime.now(UTC)},
        )


def test_duplicate_generation_is_rejected_even_after_prior_attempt_is_terminal(
    database_connection: Connection,
) -> None:
    first = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)
    database_connection.execute(
        text("UPDATE upload_sessions SET state = 'CANCELLED' WHERE id = :id"),
        {"id": first.session_id},
    )

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "INSERT INTO upload_sessions "
                "(id, blob_id, initiating_version_id, strategy, state, session_generation, "
                "part_size_bytes, planned_part_count, part_plan_schema_version, part_plan_sha256, "
                "created_at, last_activity_at) "
                "SELECT :new_id, blob_id, initiating_version_id, strategy, 'CREATED', "
                "session_generation, part_size_bytes, planned_part_count, "
                "part_plan_schema_version, part_plan_sha256, :now, :now "
                "FROM upload_sessions WHERE id = :first_id"
            ),
            {"new_id": uuid4(), "first_id": first.session_id, "now": datetime.now(UTC)},
        )


def test_only_one_active_generation_per_blob_is_allowed(database_connection: Connection) -> None:
    first = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "INSERT INTO upload_sessions "
                "(id, blob_id, initiating_version_id, strategy, state, session_generation, "
                "part_size_bytes, planned_part_count, part_plan_schema_version, part_plan_sha256, "
                "created_at, last_activity_at) "
                "SELECT :new_id, blob_id, initiating_version_id, strategy, 'CREATED', 2, "
                "part_size_bytes, planned_part_count, part_plan_schema_version, part_plan_sha256, "
                ":now, :now FROM upload_sessions WHERE id = :first_id"
            ),
            {"new_id": uuid4(), "first_id": first.session_id, "now": datetime.now(UTC)},
        )


def test_verified_part_requires_complete_matching_response_and_listing_receipts(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "UPDATE upload_parts SET state = 'VERIFIED', verified_at = :now "
                "WHERE upload_session_id = :id AND part_number = 1"
            ),
            {"id": context.session_id, "now": datetime.now(UTC)},
        )


def test_identical_etags_and_checksums_across_parts_are_valid(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)
    move_session_to_in_progress(database_connection, context)
    verify_all_parts(database_connection, context)

    _force_deferred_constraints(database_connection)

    assert database_connection.execute(
        text(
            "SELECT count(DISTINCT upload_response_etag), "
            "count(DISTINCT upload_response_checksum_sha256_base64) "
            "FROM upload_parts WHERE upload_session_id = :id"
        ),
        {"id": context.session_id},
    ).one() == (1, 1)


def test_verified_response_receipt_is_immutable_until_guarded_reset(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)
    move_session_to_in_progress(database_connection, context)
    verify_all_parts(database_connection, context)

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "UPDATE upload_parts SET upload_response_etag = 'fabricated' "
                "WHERE upload_session_id = :id AND part_number = 1"
            ),
            {"id": context.session_id},
        )


def test_new_multipart_session_cannot_skip_created_state(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)
    database_connection.execute(
        text("UPDATE upload_sessions SET state = 'CANCELLED' WHERE id = :id"),
        {"id": context.session_id},
    )

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "INSERT INTO upload_sessions "
                "(id, blob_id, initiating_version_id, strategy, state, session_generation, "
                "provider_upload_id, part_size_bytes, planned_part_count, "
                "part_plan_schema_version, part_plan_sha256, created_at, last_activity_at) "
                "SELECT :id, blob_id, initiating_version_id, 'MULTIPART', 'COMPLETED', 2, "
                "'forged', part_size_bytes, planned_part_count, part_plan_schema_version, "
                "part_plan_sha256, :now, :now FROM upload_sessions WHERE id = :source"
            ),
            {"id": uuid4(), "source": context.session_id, "now": datetime.now(UTC)},
        )


def test_completing_parts_ready_requires_every_part_verified(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)
    move_session_to_in_progress(database_connection, context)

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "UPDATE upload_sessions SET state = 'COMPLETING', "
                "completion_reason = 'PARTS_READY', completion_phase = 'PENDING', "
                "completion_requested_at = :now WHERE id = :id"
            ),
            {"id": context.session_id, "now": datetime.now(UTC)},
        )
        _force_deferred_constraints(database_connection)


def test_completing_parts_ready_accepts_receipt_backed_verified_parts(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)
    move_session_to_in_progress(database_connection, context)
    verify_all_parts(database_connection, context)
    database_connection.execute(
        text(
            "UPDATE upload_sessions SET state = 'COMPLETING', "
            "completion_reason = 'PARTS_READY', completion_phase = 'PENDING', "
            "completion_requested_at = :now WHERE id = :id"
        ),
        {"id": context.session_id, "now": datetime.now(UTC)},
    )

    _force_deferred_constraints(database_connection)


def test_completed_session_requires_available_blob_and_full_stream_evidence(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)
    move_session_to_in_progress(database_connection, context)
    verify_all_parts(database_connection, context)
    now = datetime.now(UTC)
    database_connection.execute(
        text(
            "UPDATE upload_sessions SET state = 'COMPLETING', completion_reason = 'PARTS_READY', "
            "completion_phase = 'PENDING', completion_requested_at = :now WHERE id = :id"
        ),
        {"id": context.session_id, "now": now},
    )

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text("UPDATE upload_sessions SET state = 'COMPLETED' WHERE id = :id"),
            {"id": context.session_id},
        )
        _force_deferred_constraints(database_connection)


def test_m2_idempotency_binding_cannot_be_retargeted(database_connection: Connection) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)
    request_id = uuid4()
    database_connection.execute(
        text(
            "INSERT INTO idempotency_records "
            "(scope, key, request_sha256, resource_type, resource_id, http_status, "
            "response_json, created_at) "
            "VALUES ('multipart-session-resolve', :key, :sha, 'upload-session', :resource_id, "
            "200, :response, :now)"
        ),
        {
            "key": str(request_id),
            "sha": "a" * 64,
            "resource_id": context.session_id,
            "response": '{"status":"ok"}',
            "now": datetime.now(UTC),
        },
    )

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "UPDATE idempotency_records SET resource_id = :other "
                "WHERE scope = 'multipart-session-resolve' AND key = :key"
            ),
            {"other": uuid4(), "key": str(request_id)},
        )


def test_provider_checksum_must_be_canonical_base64_of_expected_digest(
    database_connection: Connection,
) -> None:
    context = insert_multipart_session(database_connection)
    _force_deferred_constraints(database_connection)
    wrong = Sha256Digest("01" * 32).checksum_base64

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "UPDATE upload_parts SET state = 'UPLOADED', upload_response_etag = 'etag', "
                "upload_response_checksum_sha256_base64 = :checksum, "
                "upload_response_received_at = :now, uploaded_at = :now "
                "WHERE upload_session_id = :id AND part_number = 1"
            ),
            {"id": context.session_id, "checksum": wrong, "now": datetime.now(UTC)},
        )
