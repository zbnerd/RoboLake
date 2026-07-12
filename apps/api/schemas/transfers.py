"""Blob upload and one-capability download schemas."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pydantic import Field

from apps.api.schemas.datasets import StrictSchema, VersionResponse


class UploadPrepareRequest(StrictSchema):
    sha256: str = Field(min_length=64, max_length=64)


class UploadPreparationResponse(StrictSchema):
    version: VersionResponse
    blob_sha256: str
    size_bytes: int
    available: bool
    session_id: UUID | None


class PresignedRequestResponse(StrictSchema):
    url: str
    headers: Mapping[str, str]


class CompleteUploadRequest(StrictSchema):
    etag: str | None = Field(default=None, max_length=1024)


class DownloadPlanRequest(StrictSchema):
    cursor: str | None = Field(default=None, max_length=128)


class DownloadEntryResponse(StrictSchema):
    relative_path: str
    size_bytes: int
    sha256: str


class DownloadItemResponse(StrictSchema):
    entry: DownloadEntryResponse
    request: PresignedRequestResponse


class DownloadCapabilityResponse(StrictSchema):
    item: DownloadItemResponse | None
    next_cursor: str | None
    complete: bool
