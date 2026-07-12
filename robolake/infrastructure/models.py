"""SQLAlchemy 2 mappings for the RoboLake M1 registry."""

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
        Index(
            "uq_upload_sessions_active_blob",
            "blob_id",
            unique=True,
            postgresql_where=text("state IN ('CREATED','IN_PROGRESS')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    blob_id: Mapped[UUID] = mapped_column(ForeignKey("blobs.id"), index=True)
    initiating_version_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_versions.id"))
    strategy: Mapped[str] = mapped_column(String(16), default="SINGLE_PUT")
    state: Mapped[str] = mapped_column(String(16))
    etag: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
