"""PINNED_MINIO_LIVE multipart provider contract evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import pytest
from boto3.session import Session
from botocore.config import Config
from robolake.application.contracts import (
    MultipartAbortOutcome,
    MultipartCompletionOutcome,
    ProviderUploadId,
)
from robolake.domain.errors import MultipartSessionNotFoundError
from robolake.domain.identifiers import Sha256Digest
from robolake.domain.multipart import CompletedPartReceipt
from robolake.infrastructure.multipart_storage import (
    S3MultipartObjectStore,
    parse_upload_part_response,
)
from robolake.infrastructure.object_storage import create_public_s3_client, create_s3_client
from robolake.infrastructure.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.integration

PART_BYTES = 6 * 1024 * 1024


def _digest(data: bytes) -> Sha256Digest:
    return Sha256Digest.parse(hashlib.sha256(data).hexdigest())


def _container_minio_admin_client(settings: Settings) -> S3Client:
    container_id = subprocess.run(
        ["docker", "compose", "ps", "-q", "minio"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not container_id:
        raise RuntimeError("Pinned MinIO container is not running.")
    inspected = json.loads(
        subprocess.run(
            ["docker", "inspect", container_id],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    values = {
        name: value
        for item in inspected[0]["Config"]["Env"]
        if "=" in item
        for name, value in [item.split("=", 1)]
    }
    root_user = values.get("MINIO_ROOT_USER")
    root_password = values.get("MINIO_ROOT_PASSWORD")
    if not root_user or not root_password:
        raise RuntimeError("Pinned MinIO test cleanup credentials are unavailable.")
    return Session().client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=root_user,
        aws_secret_access_key=root_password,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _list_incomplete(client: S3Client, bucket: str, prefix: str) -> list[dict[str, Any]]:
    uploads: list[dict[str, Any]] = []
    key_marker: str | None = None
    upload_marker: str | None = None
    while True:
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if key_marker is not None:
            request["KeyMarker"] = key_marker
        if upload_marker is not None:
            request["UploadIdMarker"] = upload_marker
        response = client.list_multipart_uploads(**request)
        uploads.extend(cast(list[dict[str, Any]], response.get("Uploads", [])))
        if not response.get("IsTruncated", False):
            return uploads
        key_marker = cast(str, response["NextKeyMarker"])
        upload_marker = cast(str, response["NextUploadIdMarker"])


def _list_objects(client: S3Client, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    token: str | None = None
    while True:
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token is not None:
            request["ContinuationToken"] = token
        response = client.list_objects_v2(**request)
        keys.extend(str(item["Key"]) for item in response.get("Contents", []))
        if not response.get("IsTruncated", False):
            return keys
        token = cast(str, response["NextContinuationToken"])


@dataclass
class LiveMultipartCase:
    store: S3MultipartObjectStore
    internal: S3Client = field(repr=False)
    admin: S3Client = field(repr=False)
    bucket: str
    prefix: str

    def key(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def create(self, name: str) -> tuple[str, ProviderUploadId]:
        key = self.key(name)
        return key, self.store.create_multipart(key)

    def upload(
        self,
        key: str,
        upload_id: ProviderUploadId,
        part_number: int,
        data: bytes,
    ) -> tuple[httpx.Response, CompletedPartReceipt]:
        digest = _digest(data)
        capability = self.store.presign_upload_part(
            key,
            upload_id,
            part_number,
            len(data),
            digest.checksum_base64,
            60,
        )
        response = httpx.put(
            capability.request.url,
            content=data,
            headers=capability.request.headers,
            timeout=30,
        )
        assert response.status_code == 200
        receipt = parse_upload_part_response(
            part_number=part_number,
            status_code=response.status_code,
            response={
                "ETag": response.headers.get("etag"),
                "ChecksumSHA256": response.headers.get("x-amz-checksum-sha256"),
            },
            expected_sha256=digest,
        )
        return response, receipt

    def read(self, key: str) -> bytes:
        response = self.internal.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        try:
            return cast(bytes, body.read())
        finally:
            body.close()

    def cleanup_and_assert_empty(self) -> None:
        for upload in _list_incomplete(self.admin, self.bucket, self.prefix):
            self.admin.abort_multipart_upload(
                Bucket=self.bucket,
                Key=upload["Key"],
                UploadId=upload["UploadId"],
            )
        for key in _list_objects(self.admin, self.bucket, self.prefix):
            self.admin.delete_object(Bucket=self.bucket, Key=key)
        remaining_upload_count = len(_list_incomplete(self.admin, self.bucket, self.prefix))
        remaining_object_count = len(_list_objects(self.admin, self.bucket, self.prefix))
        assert remaining_upload_count == 0
        assert remaining_object_count == 0


@pytest.fixture
def live_multipart_case() -> Iterator[LiveMultipartCase]:
    settings = Settings()
    internal = create_s3_client(settings)
    case = LiveMultipartCase(
        store=S3MultipartObjectStore(
            internal,
            create_public_s3_client(settings),
            settings.s3_bucket,
        ),
        internal=internal,
        admin=_container_minio_admin_client(settings),
        bucket=settings.s3_bucket,
        prefix=f"blobs/sha256/m2-contract-{uuid4().hex}/",
    )
    try:
        yield case
    finally:
        case.cleanup_and_assert_empty()


def _mutate_query(url: str, name: str, value: str) -> str:
    split = urlsplit(url)
    query = [(key, value if key == name else current) for key, current in parse_qsl(split.query)]
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


class TestPinnedMinioLiveCapabilities:
    def test_exact_part_succeeds_and_signed_fields_cannot_be_changed(
        self, live_multipart_case: LiveMultipartCase
    ) -> None:
        case = live_multipart_case
        key, upload_id = case.create("signed-fields")
        data = bytes(index % 251 for index in range(PART_BYTES))
        digest = _digest(data)
        capability = case.store.presign_upload_part(
            key, upload_id, 1, len(data), digest.checksum_base64, 60
        )

        exact = httpx.put(
            capability.request.url,
            content=data,
            headers=capability.request.headers,
            timeout=30,
        )
        wrong_body = httpx.put(
            capability.request.url,
            content=b"x" * len(data),
            headers=capability.request.headers,
            timeout=30,
        )
        changed_checksum_headers = {
            **capability.request.headers,
            "x-amz-checksum-sha256": _digest(b"x" * len(data)).checksum_base64,
        }
        changed_checksum = httpx.put(
            capability.request.url,
            content=b"x" * len(data),
            headers=changed_checksum_headers,
            timeout=30,
        )
        omitted_checksum_headers = {
            name: value
            for name, value in capability.request.headers.items()
            if name != "x-amz-checksum-sha256"
        }
        omitted_checksum = httpx.put(
            capability.request.url,
            content=data,
            headers=omitted_checksum_headers,
            timeout=30,
        )
        short_data = data[:-1]
        wrong_length_headers = {
            **capability.request.headers,
            "Content-Length": str(len(short_data)),
        }
        wrong_length = httpx.put(
            capability.request.url,
            content=short_data,
            headers=wrong_length_headers,
            timeout=30,
        )
        changed_part = httpx.put(
            _mutate_query(capability.request.url, "partNumber", "2"),
            content=data,
            headers=capability.request.headers,
            timeout=30,
        )
        changed_upload = httpx.put(
            _mutate_query(capability.request.url, "uploadId", "different-upload"),
            content=data,
            headers=capability.request.headers,
            timeout=30,
        )

        assert exact.status_code == 200
        assert wrong_body.status_code == 400
        assert changed_checksum.status_code == 403
        assert omitted_checksum.status_code >= 400
        assert wrong_length.status_code >= 400
        assert changed_part.status_code == 403
        assert changed_upload.status_code == 403
        assert "X-Amz" not in repr(capability)
        assert upload_id.value not in repr(capability)

    def test_expired_capability_and_repeated_issuance_are_safe(
        self, live_multipart_case: LiveMultipartCase
    ) -> None:
        case = live_multipart_case
        key, upload_id = case.create("expiry")
        data = b"e" * PART_BYTES
        digest = _digest(data)
        first = case.store.presign_upload_part(
            key, upload_id, 1, len(data), digest.checksum_base64, 60
        )
        second = case.store.presign_upload_part(
            key, upload_id, 1, len(data), digest.checksum_base64, 2
        )

        assert (
            httpx.put(
                first.request.url,
                content=data,
                headers=first.request.headers,
                timeout=30,
            ).status_code
            == 200
        )
        time.sleep(3)
        expired = httpx.put(
            second.request.url,
            content=data,
            headers=second.request.headers,
            timeout=30,
        )

        assert expired.status_code == 403


class TestPinnedMinioLiveParts:
    def test_same_number_replacement_list_and_lost_receipt_reupload(
        self, live_multipart_case: LiveMultipartCase
    ) -> None:
        case = live_multipart_case
        key, upload_id = case.create("replace-and-receipt")
        first = b"a" * PART_BYTES
        second = b"b" * PART_BYTES

        case.upload(key, upload_id, 1, first)
        _, replacement_receipt = case.upload(key, upload_id, 1, second)
        listed = case.store.list_parts(key, upload_id)

        assert len(listed) == 1
        assert listed[0].checksum_sha256_base64 == _digest(second).checksum_base64
        assert listed[0].etag == replacement_receipt.response_etag

        lost_key, lost_upload_id = case.create("lost-receipt")
        digest = _digest(first)
        lost_capability = case.store.presign_upload_part(
            lost_key, lost_upload_id, 1, len(first), digest.checksum_base64, 60
        )
        lost_response = httpx.put(
            lost_capability.request.url,
            content=first,
            headers=lost_capability.request.headers,
            timeout=30,
        )
        assert lost_response.status_code == 200
        observation = case.store.list_parts(lost_key, lost_upload_id)[0]
        assert not isinstance(observation, CompletedPartReceipt)

        _, recovered_receipt = case.upload(lost_key, lost_upload_id, 1, first)
        assert recovered_receipt.response_checksum_sha256_base64 == digest.checksum_base64

    def test_identical_parts_may_share_etag_and_checksum(
        self, live_multipart_case: LiveMultipartCase
    ) -> None:
        case = live_multipart_case
        key, upload_id = case.create("identical-parts")
        data = bytes(index % 251 for index in range(PART_BYTES))

        _, first = case.upload(key, upload_id, 1, data)
        _, second = case.upload(key, upload_id, 2, data)
        listed = case.store.list_parts(key, upload_id)

        assert first.response_etag == second.response_etag
        assert first.response_checksum_sha256_base64 == second.response_checksum_sha256_base64
        assert listed[0].etag == listed[1].etag
        assert listed[0].checksum_sha256_base64 == listed[1].checksum_sha256_base64
        completed = case.store.complete_multipart(key, upload_id, (first, second))
        assert completed.outcome is MultipartCompletionOutcome.COMPLETED


class TestPinnedMinioLiveCompletion:
    def test_conditional_complete_sends_header_and_head_stays_provider_owned(
        self, live_multipart_case: LiveMultipartCase
    ) -> None:
        case = live_multipart_case
        key, upload_id = case.create("complete")
        data = b"c" * PART_BYTES
        _, receipt = case.upload(key, upload_id, 1, data)
        captured_conditions: list[str | None] = []

        def capture_condition(request: Any, **_: Any) -> None:
            value = request.headers.get("If-None-Match")
            captured_conditions.append(str(value) if value is not None else None)

        event_name = "before-sign.s3.CompleteMultipartUpload"
        unique_id = f"robolake-test-{uuid4().hex}"
        case.internal.meta.events.register(event_name, capture_condition, unique_id=unique_id)
        try:
            result = case.store.complete_multipart(key, upload_id, (receipt,))
        finally:
            case.internal.meta.events.unregister(event_name, unique_id=unique_id)

        repeated = case.store.complete_multipart(key, upload_id, (receipt,))
        inspection = case.store.inspect_final_object(key)

        assert result.outcome is MultipartCompletionOutcome.COMPLETED
        assert repeated.outcome is MultipartCompletionOutcome.PRECONDITION_LOST
        assert captured_conditions == ["*"]
        assert case.read(key) == data
        assert inspection.exists is True
        assert inspection.provider_checksum_type == "COMPOSITE"
        assert not hasattr(inspection, "sha256")

    def test_two_conditional_completions_converge_on_200_and_412(
        self, live_multipart_case: LiveMultipartCase
    ) -> None:
        case = live_multipart_case
        key = case.key("race")
        first_upload = case.store.create_multipart(key)
        second_upload = case.store.create_multipart(key)
        first_data = b"1" * PART_BYTES
        second_data = b"2" * PART_BYTES
        _, first_receipt = case.upload(key, first_upload, 1, first_data)
        _, second_receipt = case.upload(key, second_upload, 1, second_data)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                case.store.complete_multipart, key, first_upload, (first_receipt,)
            )
            second_future = executor.submit(
                case.store.complete_multipart, key, second_upload, (second_receipt,)
            )
            outcomes = {first_future.result().outcome, second_future.result().outcome}

        assert outcomes == {
            MultipartCompletionOutcome.COMPLETED,
            MultipartCompletionOutcome.PRECONDITION_LOST,
        }
        assert case.read(key) in {first_data, second_data}


class TestPinnedMinioLiveAbortAndCleanup:
    def test_abort_then_list_reports_session_not_found(
        self, live_multipart_case: LiveMultipartCase
    ) -> None:
        case = live_multipart_case
        key, upload_id = case.create("abort")
        case.upload(key, upload_id, 1, b"a" * PART_BYTES)

        result = case.store.abort_multipart(key, upload_id)

        assert result.outcome is MultipartAbortOutcome.ABORTED
        with pytest.raises(MultipartSessionNotFoundError):
            case.store.list_parts(key, upload_id)
