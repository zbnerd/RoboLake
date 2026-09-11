"""Dataset and immutable Version HTTP contract tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from apps.api.dependencies import ApiServices
from apps.api.main import create_app
from httpx import ASGITransport, AsyncClient
from robolake.application.health import HealthService
from robolake.domain.identifiers import DatasetName, DatasetReference
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest
from robolake.domain.records import (
    ContentStatus,
    DatasetRecord,
    SnapshotStatus,
    VersionRecord,
    VersionStatus,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@dataclass
class RecordingRegistry:
    created: tuple[DatasetName, str] | None = None
    dataset: DatasetRecord | None = None
    version: VersionRecord | None = None
    stored_manifest: Manifest | None = None
    register_call: tuple[UUID, Manifest, str] | None = field(default=None, init=False)

    def create_dataset(self, name: DatasetName, idempotency_key: str) -> DatasetRecord:
        self.created = (name, idempotency_key)
        self.dataset = self.dataset or DatasetRecord(uuid4(), name)
        return self.dataset

    def register_version(
        self, dataset_id: UUID, manifest: Manifest, idempotency_key: str
    ) -> VersionRecord:
        self.register_call = (dataset_id, manifest, idempotency_key)
        assert self.dataset is not None
        self.stored_manifest = manifest
        self.version = VersionRecord(
            id=uuid4(),
            dataset_id=dataset_id,
            dataset_name=self.dataset.name,
            version_number=1,
            manifest_sha256=manifest.sha256,
            state=VersionState.DRAFT,
            file_count=manifest.file_count,
            logical_bytes=manifest.logical_bytes,
            unique_blob_count=manifest.unique_blob_count,
            unique_blob_bytes=manifest.unique_blob_bytes,
        )
        return self.version

    def resolve(self, reference: DatasetReference) -> VersionRecord:
        assert self.version is not None
        assert reference == self.version.reference
        return self.version

    def status(self, version_id: UUID) -> VersionStatus:
        assert self.version is not None
        assert version_id == self.version.id
        return VersionStatus(
            self.version,
            SnapshotStatus(self.version.file_count, self.version.logical_bytes, 0, 0),
            ContentStatus(
                self.version.unique_blob_count,
                self.version.unique_blob_bytes,
                0,
                0,
            ),
        )

    def manifest(self, version_id: UUID) -> Manifest:
        assert self.version is not None
        assert version_id == self.version.id
        assert self.stored_manifest is not None
        return self.stored_manifest


def _services(registry: RecordingRegistry) -> ApiServices:
    return ApiServices(
        health=HealthService([]),
        registry=cast(Any, registry),
        transfers=cast(Any, object()),
        close=lambda: None,
    )


async def test_create_dataset_requires_key_and_parses_domain_name() -> None:
    registry = RecordingRegistry()
    app = create_app(services=_services(registry))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/v1/datasets", json={"name": "demo/pick-place"})
        response = await client.post(
            "/v1/datasets",
            json={"name": "demo/pick-place"},
            headers={"Idempotency-Key": "create-key"},
        )

    assert missing.status_code == 422
    assert response.status_code == 201
    assert response.json()["name"] == "demo/pick-place"
    assert registry.created == (DatasetName.parse("demo/pick-place"), "create-key")


async def test_register_resolve_status_and_exact_manifest_round_trip() -> None:
    registry = RecordingRegistry()
    app = create_app(services=_services(registry))
    manifest_payload = {
        "schema_version": 1,
        "entries": [
            {
                "relative_path": "camera/front.bin",
                "size_bytes": 3,
                "sha256": "a" * 64,
            }
        ],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        dataset_response = await client.post(
            "/v1/datasets",
            json={"name": "demo/pick-place"},
            headers={"Idempotency-Key": "dataset-key"},
        )
        dataset_id = dataset_response.json()["id"]
        registered = await client.post(
            f"/v1/datasets/{dataset_id}/versions",
            json=manifest_payload,
            headers={"Idempotency-Key": "version-key"},
        )
        version_id = registered.json()["id"]
        resolved = await client.get(
            "/v1/versions/resolve",
            params={"dataset": "demo/pick-place", "version": 1},
        )
        status_response = await client.get(f"/v1/versions/{version_id}")
        manifest_response = await client.get(f"/v1/versions/{version_id}/manifest")

    assert registered.status_code == 201
    assert registered.json()["reference"] == "demo/pick-place@v1"
    assert resolved.json()["id"] == version_id
    assert status_response.json()["is_empty"] is False
    assert status_response.json()["snapshot"] == {
        "file_count": 1,
        "logical_bytes": 3,
        "ready_file_count": 0,
        "ready_logical_bytes": 0,
        "completion_percent": 0.0,
    }
    assert status_response.json()["content"]["unique_blob_count"] == 1
    assert manifest_response.headers["content-type"] == "application/json"
    assert registry.stored_manifest is not None
    assert manifest_response.content == registry.stored_manifest.canonical_bytes
