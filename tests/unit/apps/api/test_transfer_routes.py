"""Upload and one-capability download HTTP contract tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from apps.api.dependencies import ApiServices
from apps.api.main import create_app
from httpx import ASGITransport, AsyncClient
from robolake.application.contracts import (
    DownloadCapability,
    DownloadItem,
    PresignedRequest,
    UploadPreparation,
)
from robolake.application.health import HealthService
from robolake.domain.identifiers import DatasetName, RelativePath, Sha256Digest
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import ManifestEntry
from robolake.domain.records import VersionRecord

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _version(state: VersionState = VersionState.UPLOADING) -> VersionRecord:
    return VersionRecord(
        id=uuid4(),
        dataset_id=uuid4(),
        dataset_name=DatasetName.parse("demo/transfers"),
        version_number=1,
        manifest_sha256=Sha256Digest.parse("b" * 64),
        state=state,
        file_count=1,
        logical_bytes=3,
        unique_blob_count=1,
        unique_blob_bytes=3,
    )


@dataclass
class RecordingTransfers:
    version: VersionRecord
    digest: Sha256Digest = field(default_factory=lambda: Sha256Digest.parse("a" * 64))
    calls: list[tuple[object, ...]] = field(default_factory=list)
    session_id: UUID = field(default_factory=uuid4)

    def prepare_upload(
        self, version_id: UUID, sha256: Sha256Digest, idempotency_key: str
    ) -> UploadPreparation:
        self.calls.append(("prepare", version_id, sha256, idempotency_key))
        return UploadPreparation(self.version, sha256, 3, False, self.session_id)

    def issue_upload_url(self, session_id: UUID) -> PresignedRequest:
        self.calls.append(("url", session_id))
        return PresignedRequest(
            "http://storage.invalid/key?X-Amz-Signature=secret",
            {"If-None-Match": "*"},
        )

    def complete_upload(self, session_id: UUID, etag: str | None = None) -> UploadPreparation:
        self.calls.append(("complete", session_id, etag))
        return UploadPreparation(self.version, self.digest, 3, True, session_id)

    def finalize(self, version_id: UUID) -> VersionRecord:
        self.calls.append(("finalize", version_id))
        return _version(VersionState.READY)

    def download_plan(self, version_id: UUID, cursor: str | None) -> DownloadCapability:
        self.calls.append(("download", version_id, cursor))
        return DownloadCapability(
            item=DownloadItem(
                ManifestEntry(RelativePath.parse("camera.bin"), 3, self.digest),
                PresignedRequest("http://storage.invalid/get?secret=yes", {}),
            ),
            next_cursor=None,
            complete=True,
        )


def _app(transfers: RecordingTransfers) -> Any:
    services = ApiServices(
        health=HealthService([]),
        registry=cast(Any, object()),
        transfers=cast(Any, transfers),
        close=lambda: None,
    )
    return create_app(services=services)


async def test_upload_prepare_url_complete_and_finalize_are_typed_control_calls() -> None:
    transfers = RecordingTransfers(_version())
    app = _app(transfers)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        prepared = await client.post(
            f"/v1/versions/{transfers.version.id}/upload-sessions",
            json={"sha256": transfers.digest.value},
            headers={"Idempotency-Key": "upload-key"},
        )
        signed = await client.post(f"/v1/upload-sessions/{transfers.session_id}/url")
        completed = await client.post(
            f"/v1/upload-sessions/{transfers.session_id}/complete",
            json={"etag": '"etag"'},
        )
        finalized = await client.post(f"/v1/versions/{transfers.version.id}/finalize")

    assert prepared.status_code == 200
    assert prepared.json()["session_id"] == str(transfers.session_id)
    assert signed.json()["headers"] == {"If-None-Match": "*"}
    assert completed.json()["available"] is True
    assert finalized.json()["state"] == "READY"
    assert transfers.calls == [
        ("prepare", transfers.version.id, transfers.digest, "upload-key"),
        ("url", transfers.session_id),
        ("complete", transfers.session_id, '"etag"'),
        ("finalize", transfers.version.id),
    ]


async def test_download_plan_has_no_limit_and_binary_bodies_are_not_proxied() -> None:
    transfers = RecordingTransfers(_version(VersionState.READY))
    app = _app(transfers)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        planned = await client.post(
            f"/v1/versions/{transfers.version.id}/download-plan",
            json={"cursor": None},
        )
        invalid_limit = await client.post(
            f"/v1/versions/{transfers.version.id}/download-plan",
            json={"cursor": None, "limit": 100},
        )
        binary = await client.post(
            f"/v1/versions/{transfers.version.id}/upload-sessions",
            content=b"dataset bytes",
            headers={
                "Content-Type": "application/octet-stream",
                "Idempotency-Key": "binary-key",
            },
        )

    assert planned.status_code == 200
    assert planned.json()["item"]["entry"]["relative_path"] == "camera.bin"
    assert planned.json()["complete"] is True
    assert invalid_limit.status_code == 422
    assert binary.status_code == 422
    assert transfers.calls == [("download", transfers.version.id, None)]
