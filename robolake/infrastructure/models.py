"""SQLAlchemy 2 mappings for the RoboLake registry."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from robolake.infrastructure.database import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DatasetModel(Base):
    __tablename__ = "datasets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class DatasetVersionModel(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "manifest_sha256", name="uq_version_dataset_manifest"),
        UniqueConstraint("dataset_id", "version_number", name="uq_version_dataset_number"),
        Index("ix_dataset_versions_dataset_created", "dataset_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("datasets.id"))
    version_number: Mapped[int] = mapped_column(BigInteger)
    manifest_schema_version: Mapped[int] = mapped_column(Integer)
    manifest_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    file_count: Mapped[int] = mapped_column(BigInteger)
    logical_bytes: Mapped[int] = mapped_column(BigInteger)
    unique_blob_count: Mapped[int] = mapped_column(BigInteger)
    unique_blob_bytes: Mapped[int] = mapped_column(BigInteger)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verifying_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BlobModel(Base):
    __tablename__ = "blobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    state: Mapped[str] = mapped_column(String(16))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetEntryModel(Base):
    __tablename__ = "dataset_entries"
    __table_args__ = (
        UniqueConstraint("dataset_version_id", "manifest_ordinal", name="uq_entry_version_ordinal"),
        Index("ix_dataset_entries_version_blob", "dataset_version_id", "blob_id"),
    )

    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id"), primary_key=True
    )
    manifest_ordinal: Mapped[int] = mapped_column(BigInteger)
    relative_path: Mapped[str] = mapped_column(Text, primary_key=True)
    blob_id: Mapped[UUID] = mapped_column(ForeignKey("blobs.id"), index=True)


class UploadSessionModel(Base):
    __tablename__ = "upload_sessions"
    __table_args__ = (
        UniqueConstraint(
            "blob_id", "session_generation", name="uq_upload_sessions_blob_generation"
        ),
        Index(
            "uq_upload_sessions_active_blob",
            "blob_id",
            unique=True,
            postgresql_where=text(
                "state IN ('CREATED','INITIATING','IN_PROGRESS','COMPLETING','ABORTING')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    blob_id: Mapped[UUID] = mapped_column(ForeignKey("blobs.id"), index=True)
    initiating_version_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_versions.id"))
    strategy: Mapped[str] = mapped_column(String(16), default="SINGLE_PUT")
    state: Mapped[str] = mapped_column(String(16))
    etag: Mapped[str | None] = mapped_column(Text)
    session_generation: Mapped[int | None] = mapped_column(Integer)
    provider_upload_id: Mapped[str | None] = mapped_column(Text)
    part_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    planned_part_count: Mapped[int | None] = mapped_column(Integer)
    part_plan_schema_version: Mapped[int | None] = mapped_column(SmallInteger)
    part_plan_sha256: Mapped[str | None] = mapped_column(String(64))
    initiation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initiation_ambiguous_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_reason: Mapped[str | None] = mapped_column(String(32))
    completion_phase: Mapped[str | None] = mapped_column(String(32))
    completion_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completion_result: Mapped[str | None] = mapped_column(String(32))
    last_provider_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    final_absence_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_attempt_invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    verification_method: Mapped[str | None] = mapped_column(String(32))
    observed_sha256: Mapped[str | None] = mapped_column(String(64))
    observed_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    verification_read_bytes: Mapped[int | None] = mapped_column(BigInteger)
    verification_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verifier_implementation: Mapped[str | None] = mapped_column(String(128))
    abort_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UploadPartModel(Base):
    __tablename__ = "upload_parts"
    __table_args__ = (
        UniqueConstraint(
            "upload_session_id", "offset_bytes", name="uq_upload_parts_session_offset"
        ),
    )

    upload_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="RESTRICT"), primary_key=True
    )
    part_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    offset_bytes: Mapped[int] = mapped_column(BigInteger)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    upload_response_etag: Mapped[str | None] = mapped_column(Text)
    upload_response_checksum_sha256_base64: Mapped[str | None] = mapped_column(String(44))
    upload_response_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    listed_etag: Mapped[str | None] = mapped_column(Text)
    listed_checksum_sha256_base64: Mapped[str | None] = mapped_column(String(44))
    listed_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    provider_listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_capability_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_capability_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capability_issue_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class MultipartAdmissionLeaseModel(Base):
    __tablename__ = "multipart_admission_leases"
    __table_args__ = (Index("ix_multipart_admission_leases_active", "expires_at"),)

    upload_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    invocation_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    epoch: Mapped[int] = mapped_column(BigInteger)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MultipartCompletionLeaseModel(Base):
    __tablename__ = "multipart_completion_leases"
    __table_args__ = (Index("ix_multipart_completion_leases_active", "expires_at"),)

    upload_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    owner_instance_id: Mapped[UUID]
    epoch: Mapped[int] = mapped_column(BigInteger)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdempotencyRecordModel(Base):
    __tablename__ = "idempotency_records"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[UUID | None]
    http_status: Mapped[int | None] = mapped_column(Integer)
    response_json: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
