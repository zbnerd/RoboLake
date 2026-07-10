from typing import Any, cast

import pytest
from robolake.infrastructure.object_storage import ObjectStorageHealthProbe

pytestmark = pytest.mark.unit


class FakeS3Client:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        return {}


def test_object_storage_probe_heads_configured_bucket() -> None:
    client = FakeS3Client()

    ObjectStorageHealthProbe(cast(Any, client), "robolake-blobs").check()

    assert client.requests == [{"Bucket": "robolake-blobs"}]
