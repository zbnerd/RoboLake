from collections.abc import Iterator
from typing import Any, cast

import pytest
from botocore.exceptions import ClientError
from robolake.domain.identifiers import Sha256Digest
from robolake.infrastructure.object_storage import ObjectStorageHealthProbe, S3ObjectStore

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


class FakePresignClient:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.calls.append((operation, kwargs))
        key = kwargs["Params"]["Key"]
        return f"{self.endpoint}/robolake-blobs/{key}?X-Amz-Signature=secret"


def test_presigned_put_uses_public_endpoint_and_exact_create_only_headers() -> None:
    internal = FakePresignClient("http://minio:9000")
    public = FakePresignClient("http://localhost:19000")
    storage = S3ObjectStore(cast(Any, internal), cast(Any, public), "robolake-blobs")
    digest = Sha256Digest.parse("a" * 64)
    key = f"blobs/sha256/aa/aa/{digest.value}"

    request = storage.presign_put(key, 123, digest.checksum_base64, 900)

    assert request.url.startswith("http://localhost:19000/")
    assert request.headers == {
        "If-None-Match": "*",
        "Content-Length": "123",
        "x-amz-checksum-sha256": digest.checksum_base64,
    }
    assert public.calls == [
        (
            "put_object",
            {
                "Params": {
                    "Bucket": "robolake-blobs",
                    "Key": key,
                    "IfNoneMatch": "*",
                    "ContentLength": 123,
                    "ChecksumSHA256": digest.checksum_base64,
                },
                "ExpiresIn": 900,
                "HttpMethod": "PUT",
            },
        )
    ]
    assert internal.calls == []


class FakeHeadClient:
    def __init__(self, response: dict[str, Any] | ClientError) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if isinstance(self.response, ClientError):
            raise self.response
        return self.response


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "provider detail"}}, "HeadObject")


def test_head_uses_provider_checksum_mode_and_ignores_user_metadata() -> None:
    digest = Sha256Digest.parse("b" * 64)
    internal = FakeHeadClient(
        {
            "ContentLength": 9,
            "ChecksumSHA256": digest.checksum_base64,
            "ETag": '"etag"',
            "Metadata": {"sha256": "f" * 64},
        }
    )
    storage = S3ObjectStore(cast(Any, internal), cast(Any, FakePresignClient("http://public")), "b")

    info = storage.head("blobs/key")

    assert info is not None
    assert info.size_bytes == 9
    assert info.checksum_sha256 == digest
    assert info.etag == '"etag"'
    assert internal.calls == [{"Bucket": "b", "Key": "blobs/key", "ChecksumMode": "ENABLED"}]

    no_system_checksum = FakeHeadClient(
        {"ContentLength": 9, "ETag": '"etag"', "Metadata": {"sha256": digest.value}}
    )
    fallback = S3ObjectStore(
        cast(Any, no_system_checksum), cast(Any, FakePresignClient("http://public")), "b"
    )
    assert fallback.head("blobs/key").checksum_sha256 is None  # type: ignore[union-attr]


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_head_maps_only_missing_codes_to_none(code: str) -> None:
    client = FakeHeadClient(_client_error(code))
    storage = S3ObjectStore(cast(Any, client), cast(Any, FakePresignClient("http://public")), "b")

    assert storage.head("missing") is None


def test_head_propagates_nonmissing_provider_errors() -> None:
    client = FakeHeadClient(_client_error("AccessDenied"))
    storage = S3ObjectStore(cast(Any, client), cast(Any, FakePresignClient("http://public")), "b")

    with pytest.raises(ClientError):
        storage.head("forbidden")


class FakeStreamingBody:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.chunk_size: int | None = None
        self.closed = False

    def iter_chunks(self, chunk_size: int) -> Iterator[bytes]:
        self.chunk_size = chunk_size
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


class FakeGetClient:
    def __init__(self, body: FakeStreamingBody) -> None:
        self.body = body
        self.calls: list[dict[str, Any]] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"Body": self.body}


def test_iter_bytes_streams_bounded_chunks_and_always_closes_body() -> None:
    body = FakeStreamingBody((b"a", b"bc"))
    internal = FakeGetClient(body)
    storage = S3ObjectStore(
        cast(Any, internal), cast(Any, FakePresignClient("http://public")), "bucket"
    )

    stream = storage.iter_bytes("key", 2)
    assert next(stream) == b"a"
    stream.close()

    assert body.chunk_size == 2
    assert body.closed is True
    assert internal.calls == [{"Bucket": "bucket", "Key": "key"}]


def test_presigned_get_uses_public_endpoint_without_extra_signed_fields() -> None:
    public = FakePresignClient("http://localhost:19000")
    storage = S3ObjectStore(cast(Any, FakeHeadClient({})), cast(Any, public), "bucket")

    request = storage.presign_get("blobs/key", 45)

    assert request.url.startswith("http://localhost:19000/")
    assert request.headers == {}
    assert public.calls == [
        (
            "get_object",
            {
                "Params": {"Bucket": "bucket", "Key": "blobs/key"},
                "ExpiresIn": 45,
                "HttpMethod": "GET",
            },
        )
    ]
