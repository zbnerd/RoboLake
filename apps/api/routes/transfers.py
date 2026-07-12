"""Blob transfer control-plane routes; no endpoint accepts file bytes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header
from robolake.application.contracts import DownloadCapability, PresignedRequest, UploadPreparation
from robolake.application.transfers import TransferService
from robolake.domain.identifiers import Sha256Digest

from apps.api.dependencies import get_transfers
from apps.api.routes.versions import _version_response
from apps.api.schemas.datasets import VersionResponse
from apps.api.schemas.transfers import (
    CompleteUploadRequest,
    DownloadCapabilityResponse,
    DownloadEntryResponse,
    DownloadItemResponse,
    DownloadPlanRequest,
    PresignedRequestResponse,
    UploadPreparationResponse,
    UploadPrepareRequest,
)

router = APIRouter(tags=["transfers"])


def _preparation_response(value: UploadPreparation) -> UploadPreparationResponse:
    return UploadPreparationResponse(
        version=_version_response(value.version),
        blob_sha256=value.blob_sha256.value,
        size_bytes=value.size_bytes,
        available=value.available,
        session_id=value.session_id,
    )


def _presigned_response(value: PresignedRequest) -> PresignedRequestResponse:
    return PresignedRequestResponse(url=value.url, headers=value.headers)


def _download_response(value: DownloadCapability) -> DownloadCapabilityResponse:
    item = None
    if value.item is not None:
        item = DownloadItemResponse(
            entry=DownloadEntryResponse(
                relative_path=value.item.entry.relative_path.value,
                size_bytes=value.item.entry.size_bytes,
                sha256=value.item.entry.sha256.value,
            ),
            request=_presigned_response(value.item.request),
        )
    return DownloadCapabilityResponse(
        item=item,
        next_cursor=value.next_cursor,
        complete=value.complete,
    )


@router.post(
    "/versions/{version_id}/upload-sessions",
    response_model=UploadPreparationResponse,
)
def prepare_upload(
    version_id: UUID,
    request: UploadPrepareRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    transfers: Annotated[TransferService, Depends(get_transfers)],
) -> UploadPreparationResponse:
    return _preparation_response(
        transfers.prepare_upload(
            version_id,
            Sha256Digest.parse(request.sha256),
            idempotency_key,
        )
    )


@router.post("/upload-sessions/{session_id}/url", response_model=PresignedRequestResponse)
def issue_upload_url(
    session_id: UUID,
    transfers: Annotated[TransferService, Depends(get_transfers)],
) -> PresignedRequestResponse:
    return _presigned_response(transfers.issue_upload_url(session_id))


@router.get("/upload-sessions/{session_id}", response_model=UploadPreparationResponse)
def upload_status(
    session_id: UUID,
    transfers: Annotated[TransferService, Depends(get_transfers)],
) -> UploadPreparationResponse:
    return _preparation_response(transfers.upload_status(session_id))


@router.post(
    "/upload-sessions/{session_id}/complete",
    response_model=UploadPreparationResponse,
)
def complete_upload(
    session_id: UUID,
    transfers: Annotated[TransferService, Depends(get_transfers)],
    request: Annotated[CompleteUploadRequest | None, Body()] = None,
) -> UploadPreparationResponse:
    return _preparation_response(
        transfers.complete_upload(session_id, request.etag if request is not None else None)
    )


@router.post("/versions/{version_id}/finalize", response_model=VersionResponse)
def finalize_version(
    version_id: UUID,
    transfers: Annotated[TransferService, Depends(get_transfers)],
) -> VersionResponse:
    return _version_response(transfers.finalize(version_id))


@router.post(
    "/versions/{version_id}/download-plan",
    response_model=DownloadCapabilityResponse,
)
def download_plan(
    version_id: UUID,
    request: DownloadPlanRequest,
    transfers: Annotated[TransferService, Depends(get_transfers)],
) -> DownloadCapabilityResponse:
    return _download_response(transfers.download_plan(version_id, request.cursor))
