"""Add the M2 multipart domain and persistence model.

Revision ID: 20260714_0003
Revises: 20260711_0002
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0003"
down_revision: str | None = "20260711_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_M2_IDEMPOTENCY_SCOPES = (
    "multipart-session-resolve",
    "multipart-admission-acquire",
    "multipart-completion-accept",
)


def _add_session_columns() -> None:
    columns = (
        sa.Column("session_generation", sa.Integer(), nullable=True),
        sa.Column("provider_upload_id", sa.Text(), nullable=True),
        sa.Column("part_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("planned_part_count", sa.Integer(), nullable=True),
        sa.Column("part_plan_schema_version", sa.SmallInteger(), nullable=True),
        sa.Column("part_plan_sha256", sa.String(64), nullable=True),
        sa.Column("initiation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initiation_ambiguous_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_reason", sa.String(32), nullable=True),
        sa.Column("completion_phase", sa.String(32), nullable=True),
        sa.Column("completion_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_completion_result", sa.String(32), nullable=True),
        sa.Column("last_provider_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_absence_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_attempt_invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_method", sa.String(32), nullable=True),
        sa.Column("observed_sha256", sa.String(64), nullable=True),
        sa.Column("observed_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("verification_read_bytes", sa.BigInteger(), nullable=True),
        sa.Column("verification_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verifier_implementation", sa.String(128), nullable=True),
        sa.Column("abort_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in columns:
        op.add_column("upload_sessions", column)


def _create_session_constraints() -> None:
    op.create_check_constraint(
        "ck_upload_sessions_strategy",
        "upload_sessions",
        "strategy IN ('SINGLE_PUT','MULTIPART')",
    )
    op.create_check_constraint(
        "ck_upload_sessions_state",
        "upload_sessions",
        "state IN ('CREATED','INITIATING','IN_PROGRESS','COMPLETING','COMPLETED',"
        "'ABORTING','ABORTED','CANCELLED','FAILED')",
    )
    op.create_check_constraint(
        "ck_upload_sessions_multipart_plan",
        "upload_sessions",
        "(strategy = 'SINGLE_PUT' AND session_generation IS NULL "
        "AND provider_upload_id IS NULL AND part_size_bytes IS NULL "
        "AND planned_part_count IS NULL AND part_plan_schema_version IS NULL "
        "AND part_plan_sha256 IS NULL) OR "
        "(strategy = 'MULTIPART' AND session_generation > 0 "
        "AND part_size_bytes BETWEEN 67108864 AND 5368709120 "
        "AND planned_part_count BETWEEN 1 AND 10000 "
        "AND part_plan_schema_version = 1 "
        "AND part_plan_sha256 ~ '^[0-9a-f]{64}$')",
    )
    op.create_check_constraint(
        "ck_upload_sessions_provider_id",
        "upload_sessions",
        "strategy = 'SINGLE_PUT' OR "
        "((state IN ('CREATED','INITIATING','CANCELLED') AND provider_upload_id IS NULL) OR "
        "(state IN ('IN_PROGRESS','COMPLETING','COMPLETED','ABORTING','ABORTED') "
        "AND provider_upload_id IS NOT NULL AND provider_upload_id <> '') OR state = 'FAILED')",
    )
    op.create_check_constraint(
        "ck_upload_sessions_completion_values",
        "upload_sessions",
        "(completion_reason IS NULL OR completion_reason IN ('PARTS_READY','FINAL_PRESENT')) "
        "AND (completion_phase IS NULL OR completion_phase IN "
        "('PENDING','ASSEMBLING','FINAL_PRESENT','FINAL_VERIFICATION')) "
        "AND (last_completion_result IS NULL OR last_completion_result IN "
        "('AMBIGUOUS','CONFLICT_409','EMBEDDED_ERROR','NO_SUCH_UPLOAD'))",
    )
    op.create_check_constraint(
        "ck_upload_sessions_verification_evidence",
        "upload_sessions",
        "(verification_method IS NULL AND observed_sha256 IS NULL "
        "AND observed_size_bytes IS NULL AND verification_read_bytes IS NULL "
        "AND verification_completed_at IS NULL AND verifier_implementation IS NULL) OR "
        "(verification_method = 'FULL_STREAM_SHA256' "
        "AND observed_sha256 ~ '^[0-9a-f]{64}$' "
        "AND observed_size_bytes >= 0 AND verification_read_bytes >= 0 "
        "AND verification_completed_at IS NOT NULL "
        "AND verifier_implementation IS NOT NULL AND verifier_implementation <> '')",
    )
    op.create_unique_constraint(
        "uq_upload_sessions_blob_generation",
        "upload_sessions",
        ["blob_id", "session_generation"],
    )
    op.create_index(
        "uq_upload_sessions_active_blob",
        "upload_sessions",
        ["blob_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('CREATED','INITIATING','IN_PROGRESS','COMPLETING','ABORTING')"
        ),
    )


def _create_parts_table() -> None:
    op.create_table(
        "upload_parts",
        sa.Column("upload_session_id", sa.Uuid(), nullable=False),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("offset_bytes", sa.BigInteger(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("upload_response_etag", sa.Text(), nullable=True),
        sa.Column("upload_response_checksum_sha256_base64", sa.String(44), nullable=True),
        sa.Column("upload_response_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("listed_etag", sa.Text(), nullable=True),
        sa.Column("listed_checksum_sha256_base64", sa.String(44), nullable=True),
        sa.Column("listed_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("provider_listed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_capability_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_capability_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capability_issue_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("part_number BETWEEN 1 AND 10000", name="ck_upload_parts_number"),
        sa.CheckConstraint("offset_bytes >= 0 AND size_bytes > 0", name="ck_upload_parts_range"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_upload_parts_sha"),
        sa.CheckConstraint(
            "state IN ('PENDING','UPLOADED','VERIFIED')", name="ck_upload_parts_state"
        ),
        sa.CheckConstraint("capability_issue_count >= 0", name="ck_upload_parts_capability_count"),
        sa.CheckConstraint(
            "(last_capability_expires_at IS NULL OR last_capability_issued_at IS NOT NULL) "
            "AND (last_capability_expires_at IS NULL OR "
            "last_capability_expires_at >= last_capability_issued_at)",
            name="ck_upload_parts_capability_times",
        ),
        sa.CheckConstraint(
            "state = 'PENDING' OR "
            "(upload_response_etag IS NOT NULL AND upload_response_etag <> '' "
            "AND upload_response_checksum_sha256_base64 ~ '^[A-Za-z0-9+/]{43}=$' "
            "AND upload_response_received_at IS NOT NULL AND uploaded_at IS NOT NULL)",
            name="ck_upload_parts_uploaded_receipt",
        ),
        sa.CheckConstraint(
            "state <> 'VERIFIED' OR "
            "(listed_etag = upload_response_etag "
            "AND listed_checksum_sha256_base64 = upload_response_checksum_sha256_base64 "
            "AND listed_size_bytes = size_bytes AND provider_listed_at IS NOT NULL "
            "AND verified_at IS NOT NULL)",
            name="ck_upload_parts_verified_observation",
        ),
        sa.ForeignKeyConstraint(["upload_session_id"], ["upload_sessions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("upload_session_id", "part_number"),
        sa.UniqueConstraint(
            "upload_session_id", "offset_bytes", name="uq_upload_parts_session_offset"
        ),
    )


def _create_lease_tables() -> None:
    op.create_table(
        "multipart_admission_leases",
        sa.Column("upload_session_id", sa.Uuid(), nullable=False),
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("epoch > 0", name="ck_multipart_admission_leases_epoch"),
        sa.CheckConstraint(
            "renewed_at >= acquired_at AND expires_at >= renewed_at",
            name="ck_multipart_admission_leases_times",
        ),
        sa.ForeignKeyConstraint(["upload_session_id"], ["upload_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("upload_session_id"),
    )
    op.create_index(
        "ix_multipart_admission_leases_active",
        "multipart_admission_leases",
        ["expires_at"],
    )
    op.create_table(
        "multipart_completion_leases",
        sa.Column("upload_session_id", sa.Uuid(), nullable=False),
        sa.Column("owner_instance_id", sa.Uuid(), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("epoch > 0", name="ck_multipart_completion_leases_epoch"),
        sa.CheckConstraint(
            "renewed_at >= acquired_at AND expires_at >= renewed_at",
            name="ck_multipart_completion_leases_times",
        ),
        sa.ForeignKeyConstraint(["upload_session_id"], ["upload_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("upload_session_id"),
    )
    op.create_index(
        "ix_multipart_completion_leases_active",
        "multipart_completion_leases",
        ["expires_at"],
    )


def _create_part_plan_functions() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE FUNCTION robolake_expected_multipart_part_size(blob_size bigint) RETURNS bigint
        LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        DECLARE
          selected bigint := 67108864;
        BEGIN
          IF blob_size <= 5000000000 OR blob_size > 5000000000000 THEN
            RAISE EXCEPTION 'multipart Blob size is outside protocol limits'
              USING ERRCODE = '23514';
          END IF;
          WHILE ((blob_size + selected - 1) / selected) > 10000 LOOP
            selected := selected * 2;
            IF selected > 5368709120 THEN
              RAISE EXCEPTION 'multipart Part size exceeds protocol maximum'
                USING ERRCODE = '23514';
            END IF;
          END LOOP;
          RETURN selected;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION robolake_validate_multipart_plan(target_session uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
          selected upload_sessions%ROWTYPE;
          blob_size bigint;
          actual_count bigint;
          actual_bytes bigint;
          invalid_ranges bigint;
          minimum_part integer;
          maximum_part integer;
          canonical text;
          actual_hash text;
        BEGIN
          SELECT * INTO selected FROM upload_sessions WHERE id = target_session;
          IF NOT FOUND OR selected.strategy <> 'MULTIPART' THEN
            RETURN;
          END IF;
          SELECT size_bytes INTO blob_size FROM blobs WHERE id = selected.blob_id;
          IF blob_size IS NULL OR
             selected.part_size_bytes <> robolake_expected_multipart_part_size(blob_size) OR
             selected.planned_part_count <> ((blob_size + selected.part_size_bytes - 1) /
                                               selected.part_size_bytes) THEN
            RAISE EXCEPTION 'multipart session summary differs from protocol plan'
              USING ERRCODE = '23514';
          END IF;

          SELECT count(*), coalesce(sum(size_bytes), 0), min(part_number), max(part_number),
                 count(*) FILTER (WHERE
                   part_number < 1 OR part_number > selected.planned_part_count OR
                   offset_bytes <> (part_number - 1)::bigint * selected.part_size_bytes OR
                   (part_number < selected.planned_part_count AND
                    size_bytes <> selected.part_size_bytes) OR
                   (part_number = selected.planned_part_count AND
                    size_bytes <> blob_size - offset_bytes)
                 )
            INTO actual_count, actual_bytes, minimum_part, maximum_part, invalid_ranges
            FROM upload_parts part
            WHERE part.upload_session_id = target_session;
          IF actual_count <> selected.planned_part_count OR actual_bytes <> blob_size OR
             minimum_part <> 1 OR maximum_part <> selected.planned_part_count OR
             invalid_ranges <> 0 THEN
            RAISE EXCEPTION 'multipart Part rows do not cover the Blob exactly once'
              USING ERRCODE = '23514';
          END IF;

          SELECT '{"schema_version":' || selected.part_plan_schema_version::text ||
                 ',"blob_size_bytes":' || blob_size::text ||
                 ',"part_size_bytes":' || selected.part_size_bytes::text ||
                 ',"part_count":' || selected.planned_part_count::text ||
                 ',"parts":[' || string_agg(
                   '{"part_number":' || part_number::text ||
                   ',"offset_bytes":' || offset_bytes::text ||
                   ',"size_bytes":' || size_bytes::text ||
                   ',"sha256":"' || sha256 || '"}', ',' ORDER BY part_number
                 ) || ']}'
            INTO canonical
            FROM upload_parts WHERE upload_session_id = target_session;
          actual_hash := encode(digest(convert_to(canonical, 'UTF8'), 'sha256'), 'hex');
          IF actual_hash <> selected.part_plan_sha256 THEN
            RAISE EXCEPTION 'multipart PartPlan digest does not match frozen rows'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$
        """
    )


def _create_guard_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION robolake_guard_upload_part() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          parent_strategy text;
          parent_state text;
          expected_checksum text;
          planned_count integer;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'multipart Part rows are immutable plan members'
              USING ERRCODE = '23514';
          END IF;
          SELECT strategy, state INTO parent_strategy, parent_state
            FROM upload_sessions WHERE id = NEW.upload_session_id;
          IF parent_strategy IS DISTINCT FROM 'MULTIPART' THEN
            RAISE EXCEPTION 'upload Part requires a multipart session' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'INSERT' THEN
            SELECT planned_part_count INTO planned_count
              FROM upload_sessions WHERE id = NEW.upload_session_id;
            IF parent_state <> 'CREATED' OR NEW.part_number > planned_count THEN
              RAISE EXCEPTION 'multipart Part insertion requires an unsealed planned position'
                USING ERRCODE = '23514';
            END IF;
          END IF;
          IF TG_OP = 'UPDATE' AND (
            NEW.upload_session_id IS DISTINCT FROM OLD.upload_session_id OR
            NEW.part_number IS DISTINCT FROM OLD.part_number OR
            NEW.offset_bytes IS DISTINCT FROM OLD.offset_bytes OR
            NEW.size_bytes IS DISTINCT FROM OLD.size_bytes OR
            NEW.sha256 IS DISTINCT FROM OLD.sha256
          ) THEN
            RAISE EXCEPTION 'multipart Part identity and range are immutable'
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND parent_state IN
             ('COMPLETED','ABORTED','CANCELLED','FAILED') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'terminal multipart session Parts are immutable'
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND NEW.state IS DISTINCT FROM OLD.state AND NOT (
            (OLD.state = 'PENDING' AND NEW.state = 'UPLOADED') OR
            (OLD.state = 'UPLOADED' AND NEW.state IN ('VERIFIED','PENDING')) OR
            (OLD.state = 'VERIFIED' AND NEW.state = 'PENDING')
          ) THEN
            RAISE EXCEPTION 'illegal multipart Part state transition' USING ERRCODE = '23514';
          END IF;
          IF NEW.upload_response_checksum_sha256_base64 IS NOT NULL THEN
            expected_checksum := encode(decode(NEW.sha256, 'hex'), 'base64');
            IF NEW.upload_response_checksum_sha256_base64 <> expected_checksum OR
               encode(decode(NEW.upload_response_checksum_sha256_base64, 'base64'), 'base64') <>
                 NEW.upload_response_checksum_sha256_base64 THEN
              RAISE EXCEPTION 'UploadPart response checksum differs from expected SHA-256'
                USING ERRCODE = '23514';
            END IF;
          END IF;
          IF NEW.listed_checksum_sha256_base64 IS NOT NULL AND
             (NEW.listed_checksum_sha256_base64 <> encode(decode(NEW.sha256, 'hex'), 'base64') OR
              encode(decode(NEW.listed_checksum_sha256_base64, 'base64'), 'base64') <>
                NEW.listed_checksum_sha256_base64) THEN
            RAISE EXCEPTION 'listed Part checksum differs from expected SHA-256'
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.state IN ('UPLOADED','VERIFIED') AND
             NEW.state <> 'PENDING' AND (
               NEW.upload_response_etag IS DISTINCT FROM OLD.upload_response_etag OR
               NEW.upload_response_checksum_sha256_base64 IS DISTINCT FROM
                 OLD.upload_response_checksum_sha256_base64 OR
               NEW.upload_response_received_at IS DISTINCT FROM OLD.upload_response_received_at
             ) THEN
            RAISE EXCEPTION 'receipt-backed multipart progress is immutable until reset'
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.state IN ('UPLOADED','VERIFIED') AND
             NEW.state = 'PENDING' AND NOT EXISTS (
               SELECT 1 FROM upload_sessions session
               WHERE session.id = NEW.upload_session_id
                 AND session.last_provider_reconciled_at IS NOT NULL
             ) THEN
            RAISE EXCEPTION 'multipart Part reset requires provider reconciliation evidence'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_upload_part
        BEFORE INSERT OR UPDATE OR DELETE ON upload_parts
        FOR EACH ROW EXECUTE FUNCTION robolake_guard_upload_part()
        """
    )
    op.execute(
        """
        CREATE FUNCTION robolake_guard_upload_session() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          unresolved_parts bigint;
          related_blob blobs%ROWTYPE;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.state <> 'CREATED' THEN
              RAISE EXCEPTION 'new upload session must begin in CREATED'
                USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.blob_id IS DISTINCT FROM OLD.blob_id OR
             NEW.initiating_version_id IS DISTINCT FROM OLD.initiating_version_id OR
             NEW.strategy IS DISTINCT FROM OLD.strategy THEN
            RAISE EXCEPTION 'upload session identity is immutable' USING ERRCODE = '23514';
          END IF;
          IF OLD.strategy = 'SINGLE_PUT' THEN
            IF OLD.state IN ('COMPLETED','FAILED') AND NEW IS DISTINCT FROM OLD THEN
              RAISE EXCEPTION 'terminal upload session is immutable' USING ERRCODE = '23514';
            END IF;
            IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
              (OLD.state = 'CREATED' AND NEW.state = 'IN_PROGRESS') OR
              (OLD.state = 'IN_PROGRESS' AND NEW.state IN ('COMPLETED','FAILED'))
            ) THEN
              RAISE EXCEPTION 'illegal upload session state transition' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.session_generation IS DISTINCT FROM OLD.session_generation OR
             NEW.part_size_bytes IS DISTINCT FROM OLD.part_size_bytes OR
             NEW.planned_part_count IS DISTINCT FROM OLD.planned_part_count OR
             NEW.part_plan_schema_version IS DISTINCT FROM OLD.part_plan_schema_version OR
             NEW.part_plan_sha256 IS DISTINCT FROM OLD.part_plan_sha256 OR
             (OLD.provider_upload_id IS NOT NULL AND
              NEW.provider_upload_id IS DISTINCT FROM OLD.provider_upload_id) THEN
            RAISE EXCEPTION 'multipart session identity and PartPlan are immutable'
              USING ERRCODE = '23514';
          END IF;
          IF OLD.state IN ('COMPLETED','ABORTED','CANCELLED','FAILED') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'terminal multipart session is immutable' USING ERRCODE = '23514';
          END IF;
          IF OLD.state = 'COMPLETING' AND NEW.state = 'COMPLETING' AND
             NEW.completion_reason IS DISTINCT FROM OLD.completion_reason THEN
            RAISE EXCEPTION 'accepted completion reason is immutable while work is pending'
              USING ERRCODE = '23514';
          END IF;
          IF OLD.state = 'COMPLETING' AND NEW.state = 'COMPLETING' AND
             NEW.completion_phase IS DISTINCT FROM OLD.completion_phase AND NOT (
               (OLD.completion_phase = 'PENDING' AND NEW.completion_phase = 'ASSEMBLING') OR
               (OLD.completion_phase = 'PENDING' AND NEW.completion_phase = 'FINAL_PRESENT' AND
                NEW.completion_reason = 'FINAL_PRESENT') OR
               (OLD.completion_phase = 'ASSEMBLING' AND
                NEW.completion_phase = 'FINAL_PRESENT') OR
               (OLD.completion_phase = 'ASSEMBLING' AND NEW.completion_phase = 'PENDING' AND
                NEW.completion_attempted_at IS NOT NULL AND
                NEW.last_completion_result IN ('AMBIGUOUS','EMBEDDED_ERROR')) OR
               (OLD.completion_phase = 'FINAL_PRESENT' AND
                NEW.completion_phase = 'FINAL_VERIFICATION') OR
               (OLD.completion_phase = 'FINAL_VERIFICATION' AND
                NEW.completion_phase = 'FINAL_PRESENT')
             ) THEN
            RAISE EXCEPTION 'illegal multipart completion phase transition'
              USING ERRCODE = '23514';
          END IF;
          IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
            (OLD.state = 'CREATED' AND NEW.state IN ('INITIATING','CANCELLED','FAILED')) OR
            (OLD.state = 'INITIATING' AND NEW.state IN ('IN_PROGRESS','FAILED')) OR
            (OLD.state = 'IN_PROGRESS' AND NEW.state IN ('COMPLETING','ABORTING','FAILED')) OR
            (OLD.state = 'COMPLETING' AND NEW.state IN ('IN_PROGRESS','COMPLETED','FAILED')) OR
            (OLD.state = 'ABORTING' AND NEW.state IN ('ABORTED','COMPLETING','FAILED'))
          ) THEN
            RAISE EXCEPTION 'illegal multipart session state transition' USING ERRCODE = '23514';
          END IF;
          IF OLD.state = 'CREATED' AND NEW.state = 'INITIATING' THEN
            PERFORM robolake_validate_multipart_plan(NEW.id);
            IF NEW.initiation_started_at IS NULL THEN
              RAISE EXCEPTION 'multipart initiation requires durable intent time'
                USING ERRCODE = '23514';
            END IF;
          END IF;
          IF OLD.state = 'INITIATING' AND NEW.state = 'FAILED' AND
             (NEW.failure_code <> 'INITIATION_AMBIGUOUS' OR
              NEW.initiation_ambiguous_at IS NULL OR NEW.provider_upload_id IS NOT NULL) THEN
            RAISE EXCEPTION 'ambiguous initiation requires exact terminal evidence'
              USING ERRCODE = '23514';
          END IF;
          IF NEW.state = 'FAILED' AND NEW.failure_code IS NULL THEN
            RAISE EXCEPTION 'failed multipart session requires a failure code'
              USING ERRCODE = '23514';
          END IF;
          IF NEW.state = 'COMPLETING' THEN
            IF NEW.completion_requested_at IS NULL OR NEW.completion_reason IS NULL OR
               NEW.completion_phase IS NULL THEN
              RAISE EXCEPTION 'multipart completion requires durable work metadata'
                USING ERRCODE = '23514';
            END IF;
            IF OLD.state <> 'COMPLETING' AND NEW.completion_phase <> 'PENDING' THEN
              RAISE EXCEPTION 'accepted completion must begin in PENDING phase'
                USING ERRCODE = '23514';
            END IF;
            IF NEW.completion_reason = 'PARTS_READY' THEN
              SELECT count(*) INTO unresolved_parts FROM upload_parts
                WHERE upload_session_id = NEW.id AND state <> 'VERIFIED';
              IF unresolved_parts <> 0 THEN
                RAISE EXCEPTION 'PARTS_READY completion requires every Part VERIFIED'
                  USING ERRCODE = '23514';
              END IF;
            END IF;
          END IF;
          IF OLD.state = 'COMPLETING' AND NEW.state = 'IN_PROGRESS' AND (
             NEW.final_absence_observed_at IS NULL OR NEW.last_provider_reconciled_at IS NULL OR
             NEW.last_completion_result NOT IN ('AMBIGUOUS','EMBEDDED_ERROR') OR
             (OLD.completion_reason = 'PARTS_READY' AND NOT EXISTS (
               SELECT 1 FROM upload_parts
               WHERE upload_session_id = NEW.id AND state <> 'VERIFIED'
             ))) THEN
            RAISE EXCEPTION 'multipart recovery lacks guarded provider evidence'
              USING ERRCODE = '23514';
          END IF;
          IF OLD.state = 'COMPLETING' AND NEW.state = 'FAILED' AND
             NEW.failure_code = 'PROVIDER_ATTEMPT_INVALIDATED' AND (
               NEW.provider_attempt_invalidated_at IS NULL OR
               NEW.last_completion_result NOT IN ('CONFLICT_409','NO_SUCH_UPLOAD')
             ) THEN
            RAISE EXCEPTION 'provider attempt invalidation lacks terminal evidence'
              USING ERRCODE = '23514';
          END IF;
          IF NEW.state = 'ABORTING' AND NEW.abort_requested_at IS NULL THEN
            RAISE EXCEPTION 'multipart abort requires a request time' USING ERRCODE = '23514';
          END IF;
          IF NEW.state = 'COMPLETED' THEN
            SELECT * INTO related_blob FROM blobs WHERE id = NEW.blob_id;
            IF OLD.completion_phase <> 'FINAL_VERIFICATION' OR
               NEW.completion_phase <> 'FINAL_VERIFICATION' OR
               related_blob.state <> 'AVAILABLE' OR
               NEW.verification_method <> 'FULL_STREAM_SHA256' OR
               NEW.observed_sha256 IS DISTINCT FROM related_blob.sha256 OR
               NEW.observed_size_bytes IS DISTINCT FROM related_blob.size_bytes OR
               NEW.verification_read_bytes IS DISTINCT FROM related_blob.size_bytes OR
               NEW.verification_completed_at IS NULL THEN
              RAISE EXCEPTION 'multipart completion lacks valid full-stream evidence'
                USING ERRCODE = '23514';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_upload_session
        BEFORE INSERT OR UPDATE ON upload_sessions
        FOR EACH ROW EXECUTE FUNCTION robolake_guard_upload_session()
        """
    )
    op.execute(
        """
        CREATE FUNCTION robolake_guard_admission_lease() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'admission lease fencing history is retained'
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'INSERT' THEN
            IF NEW.epoch <> 1 THEN
              RAISE EXCEPTION 'first admission lease epoch must be one'
                USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.upload_session_id IS DISTINCT FROM OLD.upload_session_id OR
             NEW.epoch < OLD.epoch OR NEW.renewed_at < OLD.renewed_at THEN
            RAISE EXCEPTION 'admission lease identity and epoch are monotonic'
              USING ERRCODE = '23514';
          END IF;
          IF NEW.owner_id IS DISTINCT FROM OLD.owner_id OR
             NEW.invocation_id IS DISTINCT FROM OLD.invocation_id THEN
            IF OLD.expires_at > clock_timestamp() OR NEW.epoch <> OLD.epoch + 1 OR
               NEW.acquired_at <= OLD.acquired_at THEN
              RAISE EXCEPTION 'admission takeover requires an expired prior epoch'
                USING ERRCODE = '23514';
            END IF;
          ELSIF NEW.epoch <> OLD.epoch OR NEW.acquired_at IS DISTINCT FROM OLD.acquired_at THEN
            RAISE EXCEPTION 'admission renewal cannot change epoch or acquisition time'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_admission_lease
        BEFORE INSERT OR UPDATE OR DELETE ON multipart_admission_leases
        FOR EACH ROW EXECUTE FUNCTION robolake_guard_admission_lease()
        """
    )
    op.execute(
        """
        CREATE FUNCTION robolake_guard_completion_lease() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'completion lease fencing history is retained'
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'INSERT' THEN
            IF NEW.epoch <> 1 THEN
              RAISE EXCEPTION 'first completion lease epoch must be one'
                USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.upload_session_id IS DISTINCT FROM OLD.upload_session_id OR
             NEW.epoch < OLD.epoch OR NEW.renewed_at < OLD.renewed_at THEN
            RAISE EXCEPTION 'completion lease identity and epoch are monotonic'
              USING ERRCODE = '23514';
          END IF;
          IF NEW.epoch = OLD.epoch THEN
            IF NEW.owner_instance_id IS DISTINCT FROM OLD.owner_instance_id OR
               NEW.acquired_at IS DISTINCT FROM OLD.acquired_at OR
               OLD.expires_at <= clock_timestamp() THEN
              RAISE EXCEPTION 'completion renewal requires the current unexpired owner and epoch'
                USING ERRCODE = '23514';
            END IF;
          ELSIF NEW.epoch = OLD.epoch + 1 THEN
            IF OLD.expires_at > clock_timestamp() OR NEW.acquired_at <= OLD.acquired_at THEN
              RAISE EXCEPTION 'completion takeover requires an expired prior epoch'
                USING ERRCODE = '23514';
            END IF;
          ELSE
            RAISE EXCEPTION 'completion lease epoch must renew or advance exactly once'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_completion_lease
        BEFORE INSERT OR UPDATE OR DELETE ON multipart_completion_leases
        FOR EACH ROW EXECUTE FUNCTION robolake_guard_completion_lease()
        """
    )
    op.execute(
        """
        CREATE FUNCTION robolake_guard_m2_idempotency() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND OLD.scope IN (
            'multipart-session-resolve','multipart-admission-acquire','multipart-completion-accept'
          ) THEN
            RAISE EXCEPTION 'M2 idempotency records are immutable' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND (OLD.scope IN (
            'multipart-session-resolve','multipart-admission-acquire','multipart-completion-accept'
          ) OR NEW.scope IN (
            'multipart-session-resolve','multipart-admission-acquire','multipart-completion-accept'
          )) THEN
            RAISE EXCEPTION 'M2 idempotency records are immutable' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'INSERT' AND NEW.scope IN (
            'multipart-session-resolve','multipart-admission-acquire','multipart-completion-accept'
          ) AND (
            NEW.key !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' OR
            NEW.resource_type <> 'upload-session' OR NEW.resource_id IS NULL OR
            NEW.http_status IS NULL OR NEW.response_json IS NULL
          ) THEN
            RAISE EXCEPTION 'M2 idempotency binding is incomplete or non-canonical'
              USING ERRCODE = '23514';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_m2_idempotency
        BEFORE INSERT OR UPDATE OR DELETE ON idempotency_records
        FOR EACH ROW EXECUTE FUNCTION robolake_guard_m2_idempotency()
        """
    )


def _create_publication_invariant() -> None:
    op.execute(
        """
        CREATE FUNCTION robolake_validate_m2_publication(target_blob uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
          selected_blob blobs%ROWTYPE;
          has_multipart boolean;
          has_completed boolean;
          has_valid_completion boolean;
          has_nonterminal boolean;
        BEGIN
          SELECT * INTO selected_blob FROM blobs WHERE id = target_blob;
          IF NOT FOUND THEN
            RETURN;
          END IF;
          SELECT EXISTS (
            SELECT 1 FROM upload_sessions
            WHERE blob_id = target_blob AND strategy = 'MULTIPART'
          ) INTO has_multipart;
          IF NOT has_multipart THEN
            RETURN;
          END IF;

          SELECT EXISTS (
            SELECT 1 FROM upload_sessions
            WHERE blob_id = target_blob AND strategy = 'MULTIPART' AND state = 'COMPLETED'
          ) INTO has_completed;
          SELECT EXISTS (
            SELECT 1 FROM upload_sessions session
            WHERE session.blob_id = target_blob
              AND session.strategy = 'MULTIPART'
              AND session.state = 'COMPLETED'
              AND session.completion_phase = 'FINAL_VERIFICATION'
              AND session.verification_method = 'FULL_STREAM_SHA256'
              AND session.observed_sha256 = selected_blob.sha256
              AND session.observed_size_bytes = selected_blob.size_bytes
              AND session.verification_read_bytes = selected_blob.size_bytes
              AND session.verification_completed_at IS NOT NULL
              AND session.verifier_implementation IS NOT NULL
              AND session.verifier_implementation <> ''
          ) INTO has_valid_completion;
          SELECT EXISTS (
            SELECT 1 FROM upload_sessions
            WHERE blob_id = target_blob AND strategy = 'MULTIPART'
              AND state IN ('CREATED','INITIATING','IN_PROGRESS','COMPLETING','ABORTING')
          ) INTO has_nonterminal;

          IF selected_blob.state = 'VERIFYING' THEN
            RAISE EXCEPTION 'multipart Blob cannot remain VERIFYING at transaction commit'
              USING ERRCODE = '23514';
          END IF;
          IF selected_blob.state = 'AVAILABLE' AND
             (NOT has_valid_completion OR has_nonterminal) THEN
            RAISE EXCEPTION 'AVAILABLE multipart Blob lacks one completed verified publication'
              USING ERRCODE = '23514';
          END IF;
          IF has_completed AND selected_blob.state <> 'AVAILABLE' THEN
            RAISE EXCEPTION 'completed multipart Session requires an AVAILABLE Blob'
              USING ERRCODE = '23514';
          END IF;
          IF EXISTS (
            SELECT 1 FROM upload_sessions session
            JOIN multipart_completion_leases lease
              ON lease.upload_session_id = session.id
            WHERE session.blob_id = target_blob
              AND session.strategy = 'MULTIPART'
              AND session.state = 'FAILED'
              AND session.failure_code = 'PROVIDER_ATTEMPT_INVALIDATED'
              AND lease.expires_at > clock_timestamp()
          ) THEN
            RAISE EXCEPTION 'invalidated provider attempt retains completion ownership'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION robolake_require_m2_publication_for_session() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM robolake_validate_m2_publication(
            CASE WHEN TG_OP = 'DELETE' THEN OLD.blob_id ELSE NEW.blob_id END
          );
          RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER multipart_session_publication_is_consistent
        AFTER INSERT OR UPDATE OR DELETE ON upload_sessions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION robolake_require_m2_publication_for_session()
        """
    )
    op.execute(
        """
        CREATE FUNCTION robolake_require_m2_publication_for_blob() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM robolake_validate_m2_publication(NEW.id);
          RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER multipart_blob_publication_is_consistent
        AFTER UPDATE ON blobs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION robolake_require_m2_publication_for_blob()
        """
    )


def upgrade() -> None:
    op.execute("DROP TRIGGER guard_upload_session ON upload_sessions")
    op.execute("DROP FUNCTION robolake_guard_upload_session()")
    op.drop_index("uq_upload_sessions_active_blob", table_name="upload_sessions")
    op.drop_constraint("ck_upload_sessions_strategy", "upload_sessions", type_="check")
    op.drop_constraint("ck_upload_sessions_state", "upload_sessions", type_="check")

    _add_session_columns()
    _create_session_constraints()
    _create_parts_table()
    _create_lease_tables()
    _create_part_plan_functions()
    _create_guard_functions()
    _create_publication_invariant()


def _drop_session_columns() -> None:
    names = (
        "abort_requested_at",
        "verifier_implementation",
        "verification_completed_at",
        "verification_started_at",
        "verification_read_bytes",
        "observed_size_bytes",
        "observed_sha256",
        "verification_method",
        "provider_attempt_invalidated_at",
        "final_absence_observed_at",
        "last_provider_reconciled_at",
        "last_completion_result",
        "completion_attempted_at",
        "completion_phase",
        "completion_reason",
        "completion_requested_at",
        "initiation_ambiguous_at",
        "initiation_started_at",
        "part_plan_sha256",
        "part_plan_schema_version",
        "planned_part_count",
        "part_size_bytes",
        "provider_upload_id",
        "session_generation",
    )
    for name in names:
        op.drop_column("upload_sessions", name)


def downgrade() -> None:
    m2_scopes = ",".join(f"'{scope}'" for scope in _M2_IDEMPOTENCY_SCOPES)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM upload_sessions WHERE strategy = 'MULTIPART') OR
             EXISTS (SELECT 1 FROM upload_parts) OR
             EXISTS (SELECT 1 FROM multipart_admission_leases) OR
             EXISTS (SELECT 1 FROM multipart_completion_leases) OR
             EXISTS (SELECT 1 FROM idempotency_records WHERE scope IN ({m2_scopes})) THEN
            RAISE EXCEPTION 'multipart workflow rows must be archived before downgrade';
          END IF;
        END;
        $$
        """
    )

    op.execute("DROP TRIGGER multipart_blob_publication_is_consistent ON blobs")
    op.execute("DROP FUNCTION robolake_require_m2_publication_for_blob()")
    op.execute("DROP TRIGGER multipart_session_publication_is_consistent ON upload_sessions")
    op.execute("DROP FUNCTION robolake_require_m2_publication_for_session()")
    op.execute("DROP FUNCTION robolake_validate_m2_publication(uuid)")
    op.execute("DROP TRIGGER guard_m2_idempotency ON idempotency_records")
    op.execute("DROP FUNCTION robolake_guard_m2_idempotency()")
    op.execute("DROP TRIGGER guard_completion_lease ON multipart_completion_leases")
    op.execute("DROP FUNCTION robolake_guard_completion_lease()")
    op.execute("DROP TRIGGER guard_admission_lease ON multipart_admission_leases")
    op.execute("DROP FUNCTION robolake_guard_admission_lease()")
    op.execute("DROP TRIGGER guard_upload_session ON upload_sessions")
    op.execute("DROP FUNCTION robolake_guard_upload_session()")
    op.execute("DROP TRIGGER guard_upload_part ON upload_parts")
    op.execute("DROP FUNCTION robolake_guard_upload_part()")
    op.execute("DROP FUNCTION robolake_validate_multipart_plan(uuid)")
    op.execute("DROP FUNCTION robolake_expected_multipart_part_size(bigint)")

    op.drop_index("ix_multipart_completion_leases_active", table_name="multipart_completion_leases")
    op.drop_table("multipart_completion_leases")
    op.drop_index("ix_multipart_admission_leases_active", table_name="multipart_admission_leases")
    op.drop_table("multipart_admission_leases")
    op.drop_table("upload_parts")

    op.drop_index("uq_upload_sessions_active_blob", table_name="upload_sessions")
    op.drop_constraint("uq_upload_sessions_blob_generation", "upload_sessions", type_="unique")
    for constraint in (
        "ck_upload_sessions_verification_evidence",
        "ck_upload_sessions_completion_values",
        "ck_upload_sessions_provider_id",
        "ck_upload_sessions_multipart_plan",
        "ck_upload_sessions_state",
        "ck_upload_sessions_strategy",
    ):
        op.drop_constraint(constraint, "upload_sessions", type_="check")
    _drop_session_columns()

    op.create_check_constraint(
        "ck_upload_sessions_strategy", "upload_sessions", "strategy = 'SINGLE_PUT'"
    )
    op.create_check_constraint(
        "ck_upload_sessions_state",
        "upload_sessions",
        "state IN ('CREATED','IN_PROGRESS','COMPLETED','FAILED')",
    )
    op.create_index(
        "uq_upload_sessions_active_blob",
        "upload_sessions",
        ["blob_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('CREATED','IN_PROGRESS')"),
    )
    op.execute(
        """
        CREATE FUNCTION robolake_guard_upload_session() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.blob_id IS DISTINCT FROM OLD.blob_id OR
             NEW.initiating_version_id IS DISTINCT FROM OLD.initiating_version_id OR
             NEW.strategy IS DISTINCT FROM OLD.strategy THEN
            RAISE EXCEPTION 'upload session identity is immutable' USING ERRCODE = '23514';
          END IF;
          IF OLD.state IN ('COMPLETED','FAILED') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'terminal upload session is immutable' USING ERRCODE = '23514';
          END IF;
          IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
            (OLD.state = 'CREATED' AND NEW.state = 'IN_PROGRESS') OR
            (OLD.state = 'IN_PROGRESS' AND NEW.state IN ('COMPLETED','FAILED'))
          ) THEN
            RAISE EXCEPTION 'illegal upload session state transition'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_upload_session
        BEFORE UPDATE ON upload_sessions
        FOR EACH ROW EXECUTE FUNCTION robolake_guard_upload_session()
        """
    )
