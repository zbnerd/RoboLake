"""Synthetic PostgreSQL fixtures for M2 schema contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from robolake.domain.constants import PART_PLAN_SCHEMA_VERSION
from robolake.domain.identifiers import Sha256Digest
from robolake.domain.multipart import (
    PartDefinition,
    PartPlan,
    part_plan_sha256,
    plan_multipart,
)
from sqlalchemy import Connection, text

_DEFAULT_PART_DIGEST = Sha256Digest("00" * 32)


@dataclass(frozen=True, slots=True)
class MultipartDatabaseContext:
    dataset_id: UUID
    version_id: UUID
    blob_id: UUID
    session_id: UUID
    plan: PartPlan


def build_multipart_plan(
    size_bytes: int = 5_000_000_001,
    digest: Sha256Digest = _DEFAULT_PART_DIGEST,
) -> PartPlan:
    boundaries = plan_multipart(size_bytes)
    return PartPlan.build(
        schema_version=PART_PLAN_SCHEMA_VERSION,
        blob_size_bytes=size_bytes,
        part_size_bytes=boundaries.part_size_bytes,
        parts=(
            PartDefinition(
                part_number=part.part_number,
                offset_bytes=part.offset_bytes,
                size_bytes=part.size_bytes,
                expected_sha256=digest,
            )
            for part in boundaries.parts
        ),
    )


def insert_large_registry_context(
    connection: Connection,
    *,
    name: str | None = None,
    size_bytes: int = 5_000_000_001,
) -> tuple[UUID, UUID, UUID]:
    dataset_id = uuid4()
    version_id = uuid4()
    blob_id = uuid4()
    now = datetime.now(UTC)
    digest = uuid4().hex * 2
    manifest_digest = uuid4().hex * 2
    connection.execute(
        text("INSERT INTO datasets (id, name, created_at) VALUES (:id, :name, :now)"),
        {"id": dataset_id, "name": name or f"m2/test-{dataset_id}", "now": now},
    )
    connection.execute(
        text(
            "INSERT INTO blobs "
            "(id, sha256, size_bytes, object_key, state, created_at) "
            "VALUES (:id, :sha, :size, :key, 'PENDING', :now)"
        ),
        {
            "id": blob_id,
            "sha": digest,
            "size": size_bytes,
            "key": f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}",
            "now": now,
        },
    )
    connection.execute(
        text(
            "INSERT INTO dataset_versions "
            "(id, dataset_id, version_number, manifest_schema_version, manifest_bytes, "
            "manifest_sha256, state, file_count, logical_bytes, unique_blob_count, "
            "unique_blob_bytes, created_at, sealed_at) "
            "VALUES (:id, :dataset_id, 1, 1, :manifest, :sha, 'DRAFT', 1, :size, 1, "
            ":size, :now, NULL)"
        ),
        {
            "id": version_id,
            "dataset_id": dataset_id,
            "manifest": b'{"schema_version":1,"entries":[]}',
            "sha": manifest_digest,
            "size": size_bytes,
            "now": now,
        },
    )
    connection.execute(
        text(
            "INSERT INTO dataset_entries "
            "(dataset_version_id, manifest_ordinal, relative_path, blob_id) "
            "VALUES (:version_id, 0, 'large.bin', :blob_id)"
        ),
        {"version_id": version_id, "blob_id": blob_id},
    )
    connection.execute(
        text("UPDATE dataset_versions SET sealed_at = :now WHERE id = :id"),
        {"id": version_id, "now": now},
    )
    return dataset_id, version_id, blob_id


def insert_multipart_session(
    connection: Connection,
    *,
    generation: int = 1,
    plan: PartPlan | None = None,
    name: str | None = None,
    offset_delta_at_part: tuple[int, int] | None = None,
) -> MultipartDatabaseContext:
    selected_plan = plan or build_multipart_plan()
    dataset_id, version_id, blob_id = insert_large_registry_context(
        connection,
        name=name,
        size_bytes=selected_plan.blob_size_bytes,
    )
    session_id = uuid4()
    now = datetime.now(UTC)
    connection.execute(
        text(
            "INSERT INTO upload_sessions "
            "(id, blob_id, initiating_version_id, strategy, state, session_generation, "
            "part_size_bytes, planned_part_count, part_plan_schema_version, part_plan_sha256, "
            "created_at, last_activity_at) "
            "VALUES (:id, :blob_id, :version_id, 'MULTIPART', 'CREATED', :generation, "
            ":part_size, :part_count, :schema_version, :plan_sha, :now, :now)"
        ),
        {
            "id": session_id,
            "blob_id": blob_id,
            "version_id": version_id,
            "generation": generation,
            "part_size": selected_plan.part_size_bytes,
            "part_count": selected_plan.part_count,
            "schema_version": selected_plan.schema_version,
            "plan_sha": part_plan_sha256(selected_plan).value,
            "now": now,
        },
    )
    connection.execute(
        text(
            "INSERT INTO upload_parts "
            "(upload_session_id, part_number, offset_bytes, size_bytes, sha256, state, "
            "capability_issue_count, created_at) "
            "VALUES (:session_id, :part_number, :offset_bytes, :size_bytes, :sha256, "
            "'PENDING', 0, :now)"
        ),
        [
            {
                "session_id": session_id,
                "part_number": part.part_number,
                "offset_bytes": (
                    part.offset_bytes + offset_delta_at_part[1]
                    if offset_delta_at_part is not None
                    and part.part_number == offset_delta_at_part[0]
                    else part.offset_bytes
                ),
                "size_bytes": part.size_bytes,
                "sha256": part.expected_sha256.value,
                "now": now,
            }
            for part in selected_plan.parts
        ],
    )
    return MultipartDatabaseContext(
        dataset_id=dataset_id,
        version_id=version_id,
        blob_id=blob_id,
        session_id=session_id,
        plan=selected_plan,
    )


def move_session_to_in_progress(connection: Connection, context: MultipartDatabaseContext) -> None:
    now = datetime.now(UTC)
    connection.execute(
        text(
            "UPDATE upload_sessions SET state = 'INITIATING', initiation_started_at = :now "
            "WHERE id = :id"
        ),
        {"id": context.session_id, "now": now},
    )
    connection.execute(
        text(
            "UPDATE upload_sessions SET state = 'IN_PROGRESS', provider_upload_id = :provider_id "
            "WHERE id = :id"
        ),
        {"id": context.session_id, "provider_id": f"provider-{context.session_id}"},
    )
    connection.execute(
        text("UPDATE blobs SET state = 'UPLOADING' WHERE id = :id"),
        {"id": context.blob_id},
    )


def verify_all_parts(connection: Connection, context: MultipartDatabaseContext) -> None:
    now = datetime.now(UTC)
    checksum = Sha256Digest("00" * 32).checksum_base64
    connection.execute(
        text(
            "UPDATE upload_parts SET state = 'UPLOADED', upload_response_etag = 'same-etag', "
            "upload_response_checksum_sha256_base64 = :checksum, "
            "upload_response_received_at = :now, uploaded_at = :now "
            "WHERE upload_session_id = :session_id"
        ),
        {"session_id": context.session_id, "checksum": checksum, "now": now},
    )
    connection.execute(
        text(
            "UPDATE upload_parts SET state = 'VERIFIED', listed_etag = upload_response_etag, "
            "listed_checksum_sha256_base64 = upload_response_checksum_sha256_base64, "
            "listed_size_bytes = size_bytes, provider_listed_at = :now, verified_at = :now "
            "WHERE upload_session_id = :session_id"
        ),
        {"session_id": context.session_id, "now": now},
    )
