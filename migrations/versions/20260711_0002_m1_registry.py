"""Create the M1 registry schema.

Revision ID: 20260711_0002
Revises: 20260710_0001
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_0002"
down_revision: str | None = "20260710_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("name <> '' AND octet_length(name) <= 255", name="ck_datasets_name"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.BigInteger(), nullable=False),
        sa.Column("manifest_schema_version", sa.Integer(), nullable=False),
        sa.Column("manifest_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("file_count", sa.BigInteger(), nullable=False),
        sa.Column("logical_bytes", sa.BigInteger(), nullable=False),
        sa.Column("unique_blob_count", sa.BigInteger(), nullable=False),
        sa.Column("unique_blob_bytes", sa.BigInteger(), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failure_detail", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verifying_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version_number > 0", name="ck_dataset_versions_number"),
        sa.CheckConstraint("manifest_schema_version = 1", name="ck_dataset_versions_schema"),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_dataset_versions_manifest_sha"
        ),
        sa.CheckConstraint(
            "state IN ('DRAFT','UPLOADING','VERIFYING','READY','FAILED')",
            name="ck_dataset_versions_state",
        ),
        sa.CheckConstraint(
            "file_count >= 0 AND logical_bytes >= 0 AND unique_blob_count >= 0 "
            "AND unique_blob_bytes >= 0",
            name="ck_dataset_versions_totals",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "manifest_sha256", name="uq_version_dataset_manifest"),
        sa.UniqueConstraint("dataset_id", "version_number", name="uq_version_dataset_number"),
    )
    op.create_index(
        "ix_dataset_versions_dataset_created",
        "dataset_versions",
        ["dataset_id", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION robolake_require_sealed_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM dataset_versions
            WHERE id = NEW.id AND sealed_at IS NULL
          ) THEN
            RAISE EXCEPTION 'dataset version must be sealed before commit'
              USING ERRCODE = '23514';
          END IF;
          RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER dataset_version_must_be_sealed
        AFTER INSERT OR UPDATE ON dataset_versions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION robolake_require_sealed_version()
        """
    )
    op.execute(
        """
        CREATE FUNCTION robolake_guard_dataset_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          entry_count bigint;
          minimum_ordinal bigint;
          maximum_ordinal bigint;
          actual_logical_bytes bigint;
          actual_unique_blob_count bigint;
          actual_unique_blob_bytes bigint;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'dataset version deletion is not supported in v0.1'
              USING ERRCODE = '23514';
          END IF;
          IF OLD.state = 'READY' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'READY dataset version is immutable' USING ERRCODE = '23514';
          END IF;
          IF OLD.sealed_at IS NOT NULL AND (
            NEW.dataset_id IS DISTINCT FROM OLD.dataset_id OR
            NEW.version_number IS DISTINCT FROM OLD.version_number OR
            NEW.manifest_schema_version IS DISTINCT FROM OLD.manifest_schema_version OR
            NEW.manifest_bytes IS DISTINCT FROM OLD.manifest_bytes OR
            NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256 OR
            NEW.file_count IS DISTINCT FROM OLD.file_count OR
            NEW.logical_bytes IS DISTINCT FROM OLD.logical_bytes OR
            NEW.unique_blob_count IS DISTINCT FROM OLD.unique_blob_count OR
            NEW.unique_blob_bytes IS DISTINCT FROM OLD.unique_blob_bytes OR
            NEW.sealed_at IS DISTINCT FROM OLD.sealed_at
          ) THEN
            RAISE EXCEPTION 'sealed dataset version content is immutable'
              USING ERRCODE = '23514';
          END IF;
          IF OLD.sealed_at IS NULL AND NEW.sealed_at IS NOT NULL THEN
            SELECT count(*), min(manifest_ordinal), max(manifest_ordinal)
              INTO entry_count, minimum_ordinal, maximum_ordinal
              FROM dataset_entries WHERE dataset_version_id = NEW.id;
            IF entry_count <> NEW.file_count OR
               (entry_count > 0 AND (minimum_ordinal <> 0 OR maximum_ordinal <> entry_count - 1))
            THEN
              RAISE EXCEPTION 'dataset version entries and ordinals do not match manifest totals'
                USING ERRCODE = '23514';
            END IF;
            SELECT coalesce(sum(blobs.size_bytes), 0)
              INTO actual_logical_bytes
              FROM dataset_entries
              JOIN blobs ON blobs.id = dataset_entries.blob_id
              WHERE dataset_entries.dataset_version_id = NEW.id;
            SELECT count(*), coalesce(sum(unique_blobs.size_bytes), 0)
              INTO actual_unique_blob_count, actual_unique_blob_bytes
              FROM (
                SELECT DISTINCT blobs.id, blobs.size_bytes
                FROM dataset_entries
                JOIN blobs ON blobs.id = dataset_entries.blob_id
                WHERE dataset_entries.dataset_version_id = NEW.id
              ) AS unique_blobs;
            IF actual_logical_bytes <> NEW.logical_bytes OR
               actual_unique_blob_count <> NEW.unique_blob_count OR
               actual_unique_blob_bytes <> NEW.unique_blob_bytes
            THEN
              RAISE EXCEPTION 'dataset version summaries do not match entries'
                USING ERRCODE = '23514';
            END IF;
          END IF;
          IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
            (OLD.state = 'DRAFT' AND NEW.state IN ('UPLOADING','VERIFYING')) OR
            (OLD.state = 'UPLOADING' AND NEW.state = 'VERIFYING') OR
            (OLD.state = 'VERIFYING' AND NEW.state IN ('READY','FAILED')) OR
            (OLD.state = 'FAILED' AND NEW.state = 'UPLOADING')
          ) THEN
            RAISE EXCEPTION 'illegal dataset version state transition'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_dataset_version
        BEFORE UPDATE OR DELETE ON dataset_versions
        FOR EACH ROW EXECUTE FUNCTION robolake_guard_dataset_version()
        """
    )
    op.create_table(
        "blobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failure_detail", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_blobs_sha"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_blobs_size"),
        sa.CheckConstraint(
            "object_key = 'blobs/sha256/' || substring(sha256 from 1 for 2) || '/' || "
            "substring(sha256 from 3 for 2) || '/' || sha256",
            name="ck_blobs_object_key",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING','UPLOADING','VERIFYING','AVAILABLE','FAILED')",
            name="ck_blobs_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
        sa.UniqueConstraint("sha256"),
    )
    op.execute(
        """
        CREATE FUNCTION robolake_guard_blob() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'blob deletion is not supported in v0.1' USING ERRCODE = '23514';
          END IF;
          IF NEW.sha256 IS DISTINCT FROM OLD.sha256 OR
             NEW.size_bytes IS DISTINCT FROM OLD.size_bytes OR
             NEW.object_key IS DISTINCT FROM OLD.object_key THEN
            RAISE EXCEPTION 'blob content identity is immutable' USING ERRCODE = '23514';
          END IF;
          IF OLD.state = 'AVAILABLE' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'AVAILABLE blob is immutable' USING ERRCODE = '23514';
          END IF;
          IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
            (OLD.state = 'PENDING' AND NEW.state = 'UPLOADING') OR
            (OLD.state = 'UPLOADING' AND NEW.state = 'VERIFYING') OR
            (OLD.state = 'VERIFYING' AND NEW.state IN ('AVAILABLE','FAILED')) OR
            (OLD.state = 'FAILED' AND NEW.state = 'UPLOADING')
          ) THEN
            RAISE EXCEPTION 'illegal blob state transition' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_blob
        BEFORE UPDATE OR DELETE ON blobs
        FOR EACH ROW EXECUTE FUNCTION robolake_guard_blob()
        """
    )
    op.create_table(
        "dataset_entries",
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("blob_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("manifest_ordinal >= 0", name="ck_dataset_entries_ordinal"),
        sa.CheckConstraint(
            "relative_path <> '' AND octet_length(relative_path) <= 1024",
            name="ck_dataset_entries_path",
        ),
        sa.ForeignKeyConstraint(["blob_id"], ["blobs.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.PrimaryKeyConstraint("dataset_version_id", "relative_path"),
        sa.UniqueConstraint(
            "dataset_version_id", "manifest_ordinal", name="uq_entry_version_ordinal"
        ),
    )
    op.create_index("ix_dataset_entries_blob_id", "dataset_entries", ["blob_id"])
    op.create_index(
        "ix_dataset_entries_version_blob",
        "dataset_entries",
        ["dataset_version_id", "blob_id"],
    )
    op.execute(
        """
        CREATE FUNCTION robolake_guard_dataset_entry() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          parent_id uuid;
          parent_sealed timestamptz;
        BEGIN
          parent_id := CASE WHEN TG_OP = 'DELETE'
            THEN OLD.dataset_version_id ELSE NEW.dataset_version_id END;
          SELECT sealed_at INTO parent_sealed FROM dataset_versions WHERE id = parent_id;
          IF parent_sealed IS NOT NULL THEN
            RAISE EXCEPTION 'sealed dataset version entries are immutable'
              USING ERRCODE = '23514';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_dataset_entry
        BEFORE INSERT OR UPDATE OR DELETE ON dataset_entries
        FOR EACH ROW EXECUTE FUNCTION robolake_guard_dataset_entry()
        """
    )
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("blob_id", sa.Uuid(), nullable=False),
        sa.Column("initiating_version_id", sa.Uuid(), nullable=False),
        sa.Column("strategy", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failure_detail", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("strategy = 'SINGLE_PUT'", name="ck_upload_sessions_strategy"),
        sa.CheckConstraint(
            "state IN ('CREATED','IN_PROGRESS','COMPLETED','FAILED')",
            name="ck_upload_sessions_state",
        ),
        sa.ForeignKeyConstraint(["blob_id"], ["blobs.id"]),
        sa.ForeignKeyConstraint(["initiating_version_id"], ["dataset_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upload_sessions_blob_id", "upload_sessions", ["blob_id"])
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
    op.create_table(
        "idempotency_records",
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name="ck_idempotency_sha"),
        sa.PrimaryKeyConstraint("scope", "key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.execute("DROP TRIGGER guard_upload_session ON upload_sessions")
    op.execute("DROP FUNCTION robolake_guard_upload_session()")
    op.drop_index("uq_upload_sessions_active_blob", table_name="upload_sessions")
    op.drop_index("ix_upload_sessions_blob_id", table_name="upload_sessions")
    op.drop_table("upload_sessions")
    op.drop_index("ix_dataset_entries_version_blob", table_name="dataset_entries")
    op.drop_index("ix_dataset_entries_blob_id", table_name="dataset_entries")
    op.execute("DROP TRIGGER guard_dataset_entry ON dataset_entries")
    op.execute("DROP FUNCTION robolake_guard_dataset_entry()")
    op.drop_table("dataset_entries")
    op.execute("DROP TRIGGER guard_blob ON blobs")
    op.execute("DROP FUNCTION robolake_guard_blob()")
    op.drop_table("blobs")
    op.drop_index("ix_dataset_versions_dataset_created", table_name="dataset_versions")
    op.execute("DROP TRIGGER guard_dataset_version ON dataset_versions")
    op.execute("DROP FUNCTION robolake_guard_dataset_version()")
    op.execute("DROP TRIGGER dataset_version_must_be_sealed ON dataset_versions")
    op.execute("DROP FUNCTION robolake_require_sealed_version()")
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
