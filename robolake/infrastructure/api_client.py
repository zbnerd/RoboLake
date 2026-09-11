"""Safe synchronous HTTP adapter for the RoboLake control plane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Never
from uuid import UUID

import httpx

from robolake.application.contracts import (
    DownloadCapability,
    DownloadItem,
    PresignedRequest,
    UploadPreparation,
)
from robolake.domain.errors import (
    ApiProtocolError,
    ApiUnavailableError,
    ContentConflictError,
    IdempotencyConflictError,
    IllegalTransitionError,
    NotFoundError,
    StoredObjectMismatchError,
    UploadConflictError,
)
from robolake.domain.identifiers import DatasetName, DatasetReference, RelativePath, Sha256Digest
from robolake.domain.lifecycle import FailureCode, VersionState
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import (
    ContentStatus,
    DatasetRecord,
    SnapshotStatus,
    VersionRecord,
    VersionStatus,
)


class RoboLakeApiClient:
    """Implement control-plane operations without exposing transport diagnostics."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def close(self) -> None:
        self._client.close()

    def create_dataset(self, name: DatasetName, idempotency_key: str) -> DatasetRecord:
        response = self._request(
            "POST",
            "/v1/datasets",
            json={"name": name.value},
            headers={"Idempotency-Key": idempotency_key},
        )
        payload = self._payload(response)
        try:
            return DatasetRecord(UUID(str(payload["id"])), DatasetName.parse(str(payload["name"])))
        except (KeyError, TypeError, ValueError) as error:
            raise ApiProtocolError("RoboLake API returned an invalid Dataset response.") from error

    def register_version(
        self, dataset_id: UUID, manifest: Manifest, idempotency_key: str
    ) -> VersionRecord:
        response = self._request(
            "POST",
            f"/v1/datasets/{dataset_id}/versions",
            json={
                "schema_version": manifest.schema_version,
                "entries": [
                    {
                        "relative_path": entry.relative_path.value,
                        "size_bytes": entry.size_bytes,
                        "sha256": entry.sha256.value,
                    }
                    for entry in manifest.entries
                ],
            },
            headers={"Idempotency-Key": idempotency_key},
        )
        return self._version(self._payload(response))

    def resolve(self, reference: DatasetReference) -> VersionRecord:
        response = self._request(
            "GET",
            "/v1/versions/resolve",
            params={"dataset": reference.dataset.value, "version": reference.version_number},
        )
        return self._version(self._payload(response))

    def status(self, version_id: UUID) -> VersionStatus:
        payload = self._payload(self._request("GET", f"/v1/versions/{version_id}"))
        try:
            version = self._version(payload)
            snapshot = _mapping(payload["snapshot"])
            content = _mapping(payload["content"])
            blocking = payload.get("failure_code")
            return VersionStatus(
                version,
                SnapshotStatus(
                    int(snapshot["file_count"]),
                    int(snapshot["logical_bytes"]),
                    int(snapshot["ready_file_count"]),
                    int(snapshot["ready_logical_bytes"]),
                ),
                ContentStatus(
                    int(content["unique_blob_count"]),
                    int(content["unique_blob_bytes"]),
                    int(content["available_blob_count"]),
                    int(content["available_blob_bytes"]),
                ),
                FailureCode(str(blocking)) if blocking is not None else None,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ApiProtocolError("RoboLake API returned an invalid status response.") from error

    def manifest(self, version_id: UUID) -> Manifest:
        response = self._request("GET", f"/v1/versions/{version_id}/manifest")
        return Manifest.from_canonical_bytes(response.content)

    def prepare_upload(
        self, version_id: UUID, digest: Sha256Digest, idempotency_key: str
    ) -> UploadPreparation:
        response = self._request(
            "POST",
            f"/v1/versions/{version_id}/upload-sessions",
            json={"sha256": digest.value},
            headers={"Idempotency-Key": idempotency_key},
        )
        return self._preparation(self._payload(response))

    def upload_url(self, session_id: UUID) -> PresignedRequest:
        payload = self._payload(self._request("POST", f"/v1/upload-sessions/{session_id}/url"))
        return self._presigned(payload)

    def complete_upload(self, session_id: UUID, etag: str | None) -> UploadPreparation:
        response = self._request(
            "POST",
            f"/v1/upload-sessions/{session_id}/complete",
            json={"etag": etag},
        )
        return self._preparation(self._payload(response))

    def finalize(self, version_id: UUID) -> VersionRecord:
        return self._version(
            self._payload(self._request("POST", f"/v1/versions/{version_id}/finalize"))
        )

    def download_plan(self, version_id: UUID, cursor: str | None) -> DownloadCapability:
        payload = self._payload(
            self._request(
                "POST",
                f"/v1/versions/{version_id}/download-plan",
                json={"cursor": cursor},
            )
        )
        try:
            raw_item = payload.get("item")
            item = None
            if raw_item is not None:
                item_payload = _mapping(raw_item)
                entry_payload = _mapping(item_payload["entry"])
                item = DownloadItem(
                    entry=ManifestEntry(
                        RelativePath.parse(str(entry_payload["relative_path"])),
                        int(entry_payload["size_bytes"]),
                        Sha256Digest.parse(str(entry_payload["sha256"])),
                    ),
                    request=self._presigned(_mapping(item_payload["request"])),
                )
            next_cursor = payload.get("next_cursor")
            return DownloadCapability(
                item=item,
                next_cursor=str(next_cursor) if next_cursor is not None else None,
                complete=bool(payload["complete"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ApiProtocolError("RoboLake API returned an invalid download plan.") from error

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.RequestError as error:
            raise ApiUnavailableError("RoboLake API is unavailable; retry the command.") from error
        if response.is_success:
            return response
        self._raise_api_error(response)

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any]:
        try:
            return _mapping(response.json())
        except (TypeError, ValueError) as error:
            raise ApiProtocolError("RoboLake API returned an invalid response.") from error

    @staticmethod
    def _version(payload: Mapping[str, Any]) -> VersionRecord:
        try:
            reference = DatasetReference.parse(str(payload["reference"]))
            failure_code = payload.get("failure_code")
            return VersionRecord(
                id=UUID(str(payload["id"])),
                dataset_id=UUID(str(payload["dataset_id"])),
                dataset_name=reference.dataset,
                version_number=reference.version_number,
                manifest_sha256=Sha256Digest.parse(str(payload["manifest_sha256"])),
                state=VersionState(str(payload["state"])),
                file_count=int(payload["file_count"]),
                logical_bytes=int(payload["logical_bytes"]),
                unique_blob_count=int(payload["unique_blob_count"]),
                unique_blob_bytes=int(payload["unique_blob_bytes"]),
                failure_code=(FailureCode(str(failure_code)) if failure_code is not None else None),
                failure_detail=(
                    str(payload["failure_detail"])
                    if payload.get("failure_detail") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ApiProtocolError("RoboLake API returned an invalid Version response.") from error

    def _preparation(self, payload: Mapping[str, Any]) -> UploadPreparation:
        try:
            raw_session = payload.get("session_id")
            return UploadPreparation(
                version=self._version(_mapping(payload["version"])),
                blob_sha256=Sha256Digest.parse(str(payload["blob_sha256"])),
                size_bytes=int(payload["size_bytes"]),
                available=bool(payload["available"]),
                session_id=UUID(str(raw_session)) if raw_session is not None else None,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ApiProtocolError("RoboLake API returned an invalid upload response.") from error

    @staticmethod
    def _presigned(payload: Mapping[str, Any]) -> PresignedRequest:
        try:
            headers = _mapping(payload["headers"])
            if not all(
                isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
            ):
                raise TypeError
            return PresignedRequest(str(payload["url"]), headers)
        except (KeyError, TypeError, ValueError) as error:
            raise ApiProtocolError("RoboLake API returned an invalid capability.") from error

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> Never:
        try:
            payload = _mapping(response.json())
            detail = _mapping(payload["error"])
            code = str(detail["code"])
            if not isinstance(detail.get("message"), str):
                raise TypeError
        except (KeyError, TypeError, ValueError) as error:
            raise ApiProtocolError("RoboLake API returned an invalid error response.") from error
        mapping: dict[str, type[Exception]] = {
            "UPLOAD_CONFLICT": UploadConflictError,
            "STORED_OBJECT_MISMATCH": StoredObjectMismatchError,
            "NOT_FOUND": NotFoundError,
            "IDEMPOTENCY_CONFLICT": IdempotencyConflictError,
            "CONTENT_CONFLICT": ContentConflictError,
            "ILLEGAL_TRANSITION": IllegalTransitionError,
        }
        error_type = mapping.get(code)
        if error_type is None:
            raise ApiProtocolError("RoboLake API returned an unsupported error code.")
        safe_messages = {
            "UPLOAD_CONFLICT": "Upload outcome is not visible; rerun push.",
            "STORED_OBJECT_MISMATCH": "Stored object requires operator inspection.",
            "NOT_FOUND": "Requested RoboLake resource was not found.",
            "IDEMPOTENCY_CONFLICT": "Idempotency key conflicts with an earlier request.",
            "CONTENT_CONFLICT": "RoboLake content identity conflicts with stored state.",
            "ILLEGAL_TRANSITION": "RoboLake resource is not in a valid state for this operation.",
        }
        raise error_type(safe_messages[code])


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError
    return value
