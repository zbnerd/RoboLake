"""Immutable DatasetVersion query routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from robolake.application.registry import RegistryService
from robolake.domain.identifiers import DatasetName, DatasetReference, RelativePath, Sha256Digest
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import VersionRecord, VersionStatus

from apps.api.dependencies import get_registry
from apps.api.schemas.datasets import (
    ContentStatusResponse,
    ManifestRegisterRequest,
    SnapshotStatusResponse,
    VersionResponse,
    VersionStatusResponse,
)

router = APIRouter(tags=["versions"])


def _version_response(record: VersionRecord) -> VersionResponse:
    return VersionResponse(
        id=record.id,
        dataset_id=record.dataset_id,
        reference=str(record.reference),
        manifest_sha256=record.manifest_sha256.value,
        state=record.state.value,
        file_count=record.file_count,
        logical_bytes=record.logical_bytes,
        unique_blob_count=record.unique_blob_count,
        unique_blob_bytes=record.unique_blob_bytes,
        is_empty=record.is_empty,
    )


def _status_response(value: VersionStatus) -> VersionStatusResponse:
    version = _version_response(value.version)
    return VersionStatusResponse(
        **version.model_dump(),
        snapshot=SnapshotStatusResponse(
            file_count=value.snapshot.file_count,
            logical_bytes=value.snapshot.logical_bytes,
            ready_file_count=value.snapshot.ready_file_count,
            ready_logical_bytes=value.snapshot.ready_logical_bytes,
            completion_percent=value.snapshot_completion_percent,
        ),
        content=ContentStatusResponse(
            unique_blob_count=value.content.unique_blob_count,
            unique_blob_bytes=value.content.unique_blob_bytes,
            available_blob_count=value.content.available_blob_count,
            available_blob_bytes=value.content.available_blob_bytes,
            completion_percent=value.content_completion_percent,
        ),
        failure_code=(value.version.failure_code.value if value.version.failure_code else None),
        failure_detail=value.version.failure_detail,
        next_action=value.next_action.value,
    )


@router.post(
    "/datasets/{dataset_id}/versions",
    response_model=VersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_version(
    dataset_id: UUID,
    request: ManifestRegisterRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    registry: Annotated[RegistryService, Depends(get_registry)],
) -> VersionResponse:
    manifest = Manifest.build(
        ManifestEntry(
            relative_path=RelativePath.parse(entry.relative_path),
            size_bytes=entry.size_bytes,
            sha256=Sha256Digest.parse(entry.sha256),
        )
        for entry in request.entries
    )
    return _version_response(registry.register_version(dataset_id, manifest, idempotency_key))


@router.get("/versions/resolve", response_model=VersionResponse)
def resolve_version(
    dataset: Annotated[str, Query(min_length=1, max_length=255)],
    version: Annotated[int, Query(ge=1)],
    registry: Annotated[RegistryService, Depends(get_registry)],
) -> VersionResponse:
    reference = DatasetReference(DatasetName.parse(dataset), version)
    return _version_response(registry.resolve(reference))


@router.get("/versions/{version_id}", response_model=VersionStatusResponse)
def version_status(
    version_id: UUID,
    registry: Annotated[RegistryService, Depends(get_registry)],
) -> VersionStatusResponse:
    return _status_response(registry.status(version_id))


@router.get("/versions/{version_id}/manifest", response_class=Response)
def version_manifest(
    version_id: UUID,
    registry: Annotated[RegistryService, Depends(get_registry)],
) -> Response:
    manifest = registry.manifest(version_id)
    return Response(content=manifest.canonical_bytes, media_type="application/json")
