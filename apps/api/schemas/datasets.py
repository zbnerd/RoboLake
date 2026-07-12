"""Dataset and Version HTTP schemas."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetCreateRequest(StrictSchema):
    name: str = Field(min_length=1, max_length=255)


class DatasetResponse(StrictSchema):
    id: UUID
    name: str


class ManifestEntryRequest(StrictSchema):
    relative_path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class ManifestRegisterRequest(StrictSchema):
    schema_version: Literal[1]
    entries: list[ManifestEntryRequest] = Field(max_length=100_000)


class VersionResponse(StrictSchema):
    id: UUID
    dataset_id: UUID
    reference: str
    manifest_sha256: str
    state: str
    file_count: int
    logical_bytes: int
    unique_blob_count: int
    unique_blob_bytes: int
    is_empty: bool


class SnapshotStatusResponse(StrictSchema):
    file_count: int
    logical_bytes: int
    ready_file_count: int
    ready_logical_bytes: int
    completion_percent: float | None


class ContentStatusResponse(StrictSchema):
    unique_blob_count: int
    unique_blob_bytes: int
    available_blob_count: int
    available_blob_bytes: int
    completion_percent: float | None


class VersionStatusResponse(VersionResponse):
    snapshot: SnapshotStatusResponse
    content: ContentStatusResponse
    failure_code: str | None
    failure_detail: str | None
    next_action: str
