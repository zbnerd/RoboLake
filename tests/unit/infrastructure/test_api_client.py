"""Safe RoboLake HTTP control-plane client tests."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from robolake.domain.errors import ApiProtocolError, UploadConflictError
from robolake.domain.identifiers import DatasetName
from robolake.infrastructure.api_client import RoboLakeApiClient


def test_api_client_serializes_dataset_request_and_parses_record() -> None:
    dataset_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/datasets"
        assert request.headers["Idempotency-Key"] == "caller-key"
        assert request.read() == b'{"name":"demo/pick-place"}'
        return httpx.Response(201, json={"id": str(dataset_id), "name": "demo/pick-place"})

    client = httpx.Client(
        base_url="http://api.invalid",
        transport=httpx.MockTransport(handler),
    )
    api = RoboLakeApiClient(client)

    record = api.create_dataset(DatasetName.parse("demo/pick-place"), "caller-key")

    assert record.id == dataset_id
    assert record.name == DatasetName.parse("demo/pick-place")


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        (
            {
                "error": {
                    "code": "UPLOAD_CONFLICT",
                    "message": "safe",
                    "next_action": "RETRY_PUSH",
                }
            },
            UploadConflictError,
        ),
        ({"provider": "http://secret.invalid/?token=credential"}, ApiProtocolError),
    ],
)
def test_api_client_maps_only_stable_error_envelope_without_provider_detail(
    payload: dict[str, object], error_type: type[Exception]
) -> None:
    client = httpx.Client(
        base_url="http://api.invalid",
        transport=httpx.MockTransport(lambda _: httpx.Response(409, json=payload)),
    )
    api = RoboLakeApiClient(client)

    with pytest.raises(error_type) as captured:
        api.create_dataset(DatasetName.parse("demo/error"), "key")

    assert "secret.invalid" not in str(captured.value)
    assert "credential" not in str(captured.value)
