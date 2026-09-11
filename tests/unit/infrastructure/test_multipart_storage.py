from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from botocore.exceptions import ClientError, ConnectionClosedError, ReadTimeoutError
from robolake.application.contracts import (
    ListedProviderPart,
    MultipartAbortOutcome,
    MultipartCompletionOutcome,
    ProviderUploadId,
)
from robolake.domain.errors import (
    MultipartAbortAmbiguousError,
    MultipartCompletionAmbiguousError,
    MultipartInitiationAmbiguousError,
    MultipartSessionNotFoundError,
    ProviderContractError,
    ProviderOperationRejectedError,
    TransientProviderFailureError,
)
from robolake.domain.identifiers import Sha256Digest
from robolake.domain.multipart import CompletedPartReceipt
from robolake.infrastructure.multipart_storage import (
    S3MultipartObjectStore,
    parse_upload_part_response,
)

pytestmark = pytest.mark.unit


class RecordingS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.create_response: dict[str, Any] = {"UploadId": "provider-upload-secret"}
        self.presigned_url = (
            "http://storage.invalid/bucket/key?uploadId=provider-upload-secret"
            "&X-Amz-Signature=capability-secret"
        )
        self.list_responses: list[dict[str, Any] | ClientError] = []
        self.head_response: dict[str, Any] | ClientError = {}
        self.complete_responses: list[dict[str, Any] | Exception] = []
        self.abort_responses: list[dict[str, Any] | Exception] = []

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_multipart_upload", kwargs))
        return self.create_response

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.calls.append((operation, kwargs))
        return self.presigned_url

    def list_parts(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_parts", kwargs))
        response = self.list_responses.pop(0)
        if isinstance(response, ClientError):
            raise response
        return response

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("head_object", kwargs))
        if isinstance(self.head_response, ClientError):
            raise self.head_response
        return self.head_response

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("complete_multipart_upload", kwargs))
        response = self.complete_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("abort_multipart_upload", kwargs))
        response = self.abort_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _storage(
    internal: RecordingS3Client | None = None,
    public: RecordingS3Client | None = None,
) -> tuple[S3MultipartObjectStore, RecordingS3Client, RecordingS3Client]:
    internal_client = internal or RecordingS3Client()
    public_client = public or RecordingS3Client()
    return (
        S3MultipartObjectStore(
            cast(Any, internal_client), cast(Any, public_client), "robolake-blobs"
        ),
        internal_client,
        public_client,
    )


def _client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "provider-upload-secret raw detail"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


class TestProviderContractDefensiveReceipt:
    def test_upload_response_receipt_requires_etag_and_matching_checksum(self) -> None:
        expected = Sha256Digest.parse("a" * 64)

        receipt = parse_upload_part_response(
            part_number=3,
            status_code=200,
            response={"ETag": '"opaque"', "ChecksumSHA256": expected.checksum_base64},
            expected_sha256=expected,
        )

        assert receipt.part_number == 3
        assert receipt.response_etag == '"opaque"'
        assert receipt.response_checksum_sha256_base64 == expected.checksum_base64

    @pytest.mark.parametrize(
        "response",
        [
            {"ChecksumSHA256": Sha256Digest.parse("a" * 64).checksum_base64},
            {"ETag": '"opaque"'},
            {"ETag": "", "ChecksumSHA256": Sha256Digest.parse("a" * 64).checksum_base64},
            {"ETag": '"opaque"', "ChecksumSHA256": "not-base64"},
            {
                "ETag": "line\nbreak",
                "ChecksumSHA256": Sha256Digest.parse("a" * 64).checksum_base64,
            },
            {
                "ETag": '"opaque"',
                "ChecksumSHA256": Sha256Digest.parse("b" * 64).checksum_base64,
            },
        ],
    )
    def test_upload_response_receipt_rejects_incomplete_or_mismatching_response(
        self, response: dict[str, str]
    ) -> None:
        with pytest.raises(ProviderContractError):
            parse_upload_part_response(
                part_number=3,
                status_code=200,
                response=response,
                expected_sha256=Sha256Digest.parse("a" * 64),
            )

    def test_create_rejects_missing_upload_id(self) -> None:
        internal = RecordingS3Client()
        internal.create_response = {}
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(ProviderContractError):
            storage.create_multipart("key")

    def test_upload_response_loss_cannot_construct_a_receipt(self) -> None:
        with pytest.raises(ProviderContractError):
            parse_upload_part_response(
                part_number=1,
                status_code=200,
                response=cast(Any, None),
                expected_sha256=Sha256Digest.parse("a" * 64),
            )

    def test_non_successful_upload_response_cannot_construct_a_receipt(self) -> None:
        expected = Sha256Digest.parse("a" * 64)

        with pytest.raises(ProviderContractError):
            parse_upload_part_response(
                part_number=1,
                status_code=400,
                response={"ETag": '"opaque"', "ChecksumSHA256": expected.checksum_base64},
                expected_sha256=expected,
            )


class TestAwsDocumentedSyntheticPresigning:
    def test_create_uses_sha256_profile_and_returns_redacted_opaque_id(self) -> None:
        storage, internal, _ = _storage()

        upload_id = storage.create_multipart("blobs/sha256/aa/aa/" + "a" * 64)

        assert upload_id.value == "provider-upload-secret"
        assert "provider-upload-secret" not in repr(upload_id)
        assert internal.calls == [
            (
                "create_multipart_upload",
                {
                    "Bucket": "robolake-blobs",
                    "Key": "blobs/sha256/aa/aa/" + "a" * 64,
                    "ChecksumAlgorithm": "SHA256",
                },
            )
        ]

    def test_one_part_capability_binds_upload_number_length_and_checksum(self) -> None:
        storage, internal, public = _storage()
        expected = Sha256Digest.parse("c" * 64)

        capability = storage.presign_upload_part(
            "blobs/sha256/cc/cc/" + expected.value,
            ProviderUploadId("provider-upload-secret"),
            part_number=9,
            size_bytes=6 * 1024 * 1024,
            checksum_sha256_base64=expected.checksum_base64,
            expires_seconds=900,
        )

        assert capability.part_number == 9
        assert capability.size_bytes == 6 * 1024 * 1024
        assert capability.expected_sha256 == expected
        assert capability.request.headers == {
            "Content-Length": str(6 * 1024 * 1024),
            "x-amz-checksum-sha256": expected.checksum_base64,
        }
        assert public.calls == [
            (
                "upload_part",
                {
                    "Params": {
                        "Bucket": "robolake-blobs",
                        "Key": "blobs/sha256/cc/cc/" + expected.value,
                        "UploadId": "provider-upload-secret",
                        "PartNumber": 9,
                        "ContentLength": 6 * 1024 * 1024,
                        "ChecksumSHA256": expected.checksum_base64,
                    },
                    "ExpiresIn": 900,
                    "HttpMethod": "PUT",
                },
            )
        ]
        assert internal.calls == []
        assert "provider-upload-secret" not in repr(capability)
        assert "X-Amz-Signature" not in repr(capability)

    @pytest.mark.parametrize("provider_url", ["", "http://["])
    def test_presign_rejects_malformed_provider_url(self, provider_url: str) -> None:
        public = RecordingS3Client()
        public.presigned_url = provider_url
        storage, _, _ = _storage(public=public)

        with pytest.raises(ProviderContractError):
            storage.presign_upload_part(
                "key",
                ProviderUploadId("provider-upload-secret"),
                part_number=1,
                size_bytes=6 * 1024 * 1024,
                checksum_sha256_base64=Sha256Digest.parse("c" * 64).checksum_base64,
                expires_seconds=60,
            )


class TestProviderContractDefensiveListingAndHead:
    def test_list_parts_consumes_all_pages_and_returns_ordered_observations(self) -> None:
        checksum = Sha256Digest.parse("d" * 64).checksum_base64
        internal = RecordingS3Client()
        internal.list_responses = [
            {
                "IsTruncated": True,
                "NextPartNumberMarker": 2,
                "Parts": [
                    {
                        "PartNumber": 2,
                        "Size": 7,
                        "ETag": '"same"',
                        "ChecksumSHA256": checksum,
                        "LastModified": datetime(2026, 7, 14, tzinfo=UTC),
                    }
                ],
            },
            {
                "IsTruncated": False,
                "Parts": [
                    {
                        "PartNumber": 1,
                        "Size": 6,
                        "ETag": '"same"',
                        "ChecksumSHA256": checksum,
                    }
                ],
            },
        ]
        storage, _, _ = _storage(internal=internal)

        parts = storage.list_parts("key", ProviderUploadId("provider-upload-secret"))

        assert [part.part_number for part in parts] == [1, 2]
        assert parts[0].etag == parts[1].etag
        assert internal.calls == [
            (
                "list_parts",
                {
                    "Bucket": "robolake-blobs",
                    "Key": "key",
                    "UploadId": "provider-upload-secret",
                },
            ),
            (
                "list_parts",
                {
                    "Bucket": "robolake-blobs",
                    "Key": "key",
                    "UploadId": "provider-upload-secret",
                    "PartNumberMarker": 2,
                },
            ),
        ]

    def test_list_parts_rejects_nonadvancing_marker(self) -> None:
        internal = RecordingS3Client()
        internal.list_responses = [{"IsTruncated": True, "NextPartNumberMarker": 0, "Parts": []}]
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(ProviderContractError):
            storage.list_parts("key", ProviderUploadId("provider-upload-secret"))

    def test_list_parts_rejects_duplicate_part_numbers_across_pages(self) -> None:
        checksum = Sha256Digest.parse("d" * 64).checksum_base64
        duplicate = {
            "PartNumber": 1,
            "Size": 6,
            "ETag": '"etag"',
            "ChecksumSHA256": checksum,
        }
        internal = RecordingS3Client()
        internal.list_responses = [
            {"IsTruncated": True, "NextPartNumberMarker": 1, "Parts": [duplicate]},
            {"IsTruncated": False, "Parts": [duplicate]},
        ]
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(ProviderContractError):
            storage.list_parts("key", ProviderUploadId("provider-upload-secret"))

    @pytest.mark.parametrize(
        "response",
        [
            {},
            {"IsTruncated": "false", "Parts": []},
            {"IsTruncated": False, "Parts": "not-a-list"},
            {
                "IsTruncated": False,
                "Parts": [
                    {
                        "PartNumber": 10_001,
                        "Size": 1,
                        "ETag": '"etag"',
                        "ChecksumSHA256": Sha256Digest.parse("d" * 64).checksum_base64,
                    }
                ],
            },
        ],
    )
    def test_list_parts_rejects_malformed_provider_pages(self, response: dict[str, Any]) -> None:
        internal = RecordingS3Client()
        internal.list_responses = [response]
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(ProviderContractError):
            storage.list_parts("key", ProviderUploadId("provider-upload-secret"))

    def test_list_parts_translates_no_such_upload_without_leaking_id(self) -> None:
        internal = RecordingS3Client()
        internal.list_responses = [_client_error("NoSuchUpload", 404, "ListParts")]
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(MultipartSessionNotFoundError) as caught:
            storage.list_parts("key", ProviderUploadId("provider-upload-secret"))

        assert "provider-upload-secret" not in str(caught.value)

    def test_head_preserves_composite_checksum_as_provider_metadata(self) -> None:
        internal = RecordingS3Client()
        internal.head_response = {
            "ContentLength": 12,
            "ETag": '"multipart-2"',
            "ChecksumSHA256": "provider-composite-checksum-2",
            "ChecksumType": "COMPOSITE",
        }
        storage, _, _ = _storage(internal=internal)

        inspection = storage.inspect_final_object("key")

        assert inspection.exists is True
        assert inspection.size_bytes == 12
        assert inspection.provider_checksum_sha256 == "provider-composite-checksum-2"
        assert inspection.provider_checksum_type == "COMPOSITE"
        assert not hasattr(inspection, "sha256")
        assert internal.calls == [
            (
                "head_object",
                {"Bucket": "robolake-blobs", "Key": "key", "ChecksumMode": "ENABLED"},
            )
        ]

    def test_head_missing_is_not_an_integrity_error(self) -> None:
        internal = RecordingS3Client()
        internal.head_response = _client_error("NoSuchKey", 404, "HeadObject")
        storage, _, _ = _storage(internal=internal)

        inspection = storage.inspect_final_object("key")

        assert inspection.exists is False
        assert inspection.size_bytes is None

    def test_head_rejects_malformed_provider_response(self) -> None:
        internal = RecordingS3Client()
        internal.head_response = {"ContentLength": 12}
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(ProviderContractError):
            storage.inspect_final_object("key")

    @pytest.mark.parametrize("operation", ["list", "head"])
    def test_read_response_loss_is_retryable_and_redacted(self, operation: str) -> None:
        secret_url = "http://provider/?uploadId=provider-upload-secret"

        class FailingReadClient(RecordingS3Client):
            def list_parts(self, **kwargs: Any) -> dict[str, Any]:
                raise ReadTimeoutError(endpoint_url=secret_url)

            def head_object(self, **kwargs: Any) -> dict[str, Any]:
                raise ReadTimeoutError(endpoint_url=secret_url)

        storage, _, _ = _storage(internal=FailingReadClient())

        with pytest.raises(TransientProviderFailureError) as caught:
            if operation == "list":
                storage.list_parts("key", ProviderUploadId("provider-upload-secret"))
            else:
                storage.inspect_final_object("key")

        assert "provider-upload-secret" not in str(caught.value)


def _receipts() -> tuple[CompletedPartReceipt, ...]:
    checksum = Sha256Digest.parse("e" * 64).checksum_base64
    return (
        CompletedPartReceipt(2, '"etag-2"', checksum),
        CompletedPartReceipt(1, '"etag-1"', checksum),
    )


class TestAwsDocumentedSyntheticCompletion:
    def test_complete_sorts_receipts_and_always_sends_create_only_condition(self) -> None:
        internal = RecordingS3Client()
        internal.complete_responses = [
            {"ETag": '"multipart-2"', "ResponseMetadata": {"HTTPStatusCode": 200}}
        ]
        storage, _, _ = _storage(internal=internal)

        result = storage.complete_multipart(
            "key", ProviderUploadId("provider-upload-secret"), _receipts()
        )

        assert result.outcome is MultipartCompletionOutcome.COMPLETED
        assert internal.calls == [
            (
                "complete_multipart_upload",
                {
                    "Bucket": "robolake-blobs",
                    "Key": "key",
                    "UploadId": "provider-upload-secret",
                    "MultipartUpload": {
                        "Parts": [
                            {
                                "PartNumber": 1,
                                "ETag": '"etag-1"',
                                "ChecksumSHA256": _receipts()[1].response_checksum_sha256_base64,
                            },
                            {
                                "PartNumber": 2,
                                "ETag": '"etag-2"',
                                "ChecksumSHA256": _receipts()[0].response_checksum_sha256_base64,
                            },
                        ]
                    },
                    "IfNoneMatch": "*",
                },
            )
        ]

    @pytest.mark.parametrize(
        ("code", "status", "outcome"),
        [
            ("PreconditionFailed", 412, MultipartCompletionOutcome.PRECONDITION_LOST),
            (
                "ConditionalRequestConflict",
                409,
                MultipartCompletionOutcome.CONDITIONAL_CONFLICT,
            ),
        ],
    )
    def test_complete_translates_conditional_outcomes_without_retry(
        self, code: str, status: int, outcome: MultipartCompletionOutcome
    ) -> None:
        internal = RecordingS3Client()
        internal.complete_responses = [_client_error(code, status, "CompleteMultipartUpload")]
        storage, _, _ = _storage(internal=internal)

        result = storage.complete_multipart(
            "key", ProviderUploadId("provider-upload-secret"), _receipts()
        )

        assert result.outcome is outcome
        assert len(internal.calls) == 1

    def test_complete_translates_no_such_upload_without_expiry_guess(self) -> None:
        internal = RecordingS3Client()
        internal.complete_responses = [
            _client_error("NoSuchUpload", 404, "CompleteMultipartUpload")
        ]
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(MultipartSessionNotFoundError):
            storage.complete_multipart(
                "key", ProviderUploadId("provider-upload-secret"), _receipts()
            )

    def test_complete_embedded_200_error_is_not_success(self) -> None:
        internal = RecordingS3Client()
        internal.complete_responses = [
            _client_error("InternalError", 200, "CompleteMultipartUpload")
        ]
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(MultipartCompletionAmbiguousError) as caught:
            storage.complete_multipart(
                "key", ProviderUploadId("provider-upload-secret"), _receipts()
            )

        assert "provider-upload-secret" not in str(caught.value)

    @pytest.mark.parametrize(
        "failure",
        [
            ReadTimeoutError(
                endpoint_url=(
                    "http://storage.invalid/key?uploadId=provider-upload-secret"
                    "&X-Amz-Signature=capability-secret"
                )
            ),
            ConnectionClosedError(
                endpoint_url=(
                    "http://storage.invalid/key?uploadId=provider-upload-secret"
                    "&X-Amz-Signature=capability-secret"
                )
            ),
        ],
    )
    def test_complete_response_loss_is_ambiguous_and_redacted(self, failure: Exception) -> None:
        internal = RecordingS3Client()
        internal.complete_responses = [failure]
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(MultipartCompletionAmbiguousError) as caught:
            storage.complete_multipart(
                "key", ProviderUploadId("provider-upload-secret"), _receipts()
            )

        assert "provider-upload-secret" not in str(caught.value)
        assert "X-Amz" not in str(caught.value)


class TestProviderContractDefensiveCompletion:
    @pytest.mark.parametrize(
        "receipts",
        [
            (),
            (CompletedPartReceipt(2, '"etag"', Sha256Digest.parse("e" * 64).checksum_base64),),
            (
                CompletedPartReceipt(1, '"etag"', Sha256Digest.parse("e" * 64).checksum_base64),
                CompletedPartReceipt(1, '"etag"', Sha256Digest.parse("e" * 64).checksum_base64),
            ),
        ],
    )
    def test_complete_rejects_empty_gapped_or_duplicate_receipts(
        self, receipts: tuple[CompletedPartReceipt, ...]
    ) -> None:
        storage, internal, _ = _storage()

        with pytest.raises(ProviderContractError):
            storage.complete_multipart("key", ProviderUploadId("provider-upload-secret"), receipts)

        assert internal.calls == []

    def test_complete_rejects_malformed_success_response(self) -> None:
        internal = RecordingS3Client()
        internal.complete_responses = [{"ResponseMetadata": {"HTTPStatusCode": 200}}]
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(ProviderContractError):
            storage.complete_multipart(
                "key", ProviderUploadId("provider-upload-secret"), _receipts()
            )

    def test_list_parts_observation_cannot_be_used_as_complete_receipt(self) -> None:
        storage, internal, _ = _storage()
        listed = ListedProviderPart(
            part_number=1,
            size_bytes=6,
            etag='"listed"',
            checksum_sha256_base64=Sha256Digest.parse("e" * 64).checksum_base64,
            modified_at=None,
        )

        with pytest.raises(ProviderContractError):
            storage.complete_multipart(
                "key",
                ProviderUploadId("provider-upload-secret"),
                cast(Any, (listed,)),
            )

        assert internal.calls == []

    @pytest.mark.parametrize("etag", [cast(Any, 123), "line\nbreak"])
    def test_complete_rejects_malformed_response_etag(self, etag: str) -> None:
        storage, internal, _ = _storage()
        receipt = CompletedPartReceipt(
            1,
            etag,
            Sha256Digest.parse("e" * 64).checksum_base64,
        )

        with pytest.raises(ProviderContractError):
            storage.complete_multipart(
                "key", ProviderUploadId("provider-upload-secret"), (receipt,)
            )

        assert internal.calls == []


class TestAwsDocumentedSyntheticAbort:
    def test_abort_returns_confirmed_result(self) -> None:
        internal = RecordingS3Client()
        internal.abort_responses = [{"ResponseMetadata": {"HTTPStatusCode": 204}}]
        storage, _, _ = _storage(internal=internal)

        result = storage.abort_multipart("key", ProviderUploadId("provider-upload-secret"))

        assert result.outcome is MultipartAbortOutcome.ABORTED
        assert internal.calls == [
            (
                "abort_multipart_upload",
                {
                    "Bucket": "robolake-blobs",
                    "Key": "key",
                    "UploadId": "provider-upload-secret",
                },
            )
        ]

    def test_abort_no_such_upload_is_already_absent(self) -> None:
        internal = RecordingS3Client()
        internal.abort_responses = [_client_error("NoSuchUpload", 404, "AbortMultipartUpload")]
        storage, _, _ = _storage(internal=internal)

        result = storage.abort_multipart("key", ProviderUploadId("provider-upload-secret"))

        assert result.outcome is MultipartAbortOutcome.ALREADY_ABSENT

    def test_abort_response_loss_is_ambiguous_and_redacted(self) -> None:
        internal = RecordingS3Client()
        internal.abort_responses = [
            ReadTimeoutError(endpoint_url="http://provider/?uploadId=provider-upload-secret")
        ]
        storage, _, _ = _storage(internal=internal)

        with pytest.raises(MultipartAbortAmbiguousError) as caught:
            storage.abort_multipart("key", ProviderUploadId("provider-upload-secret"))

        assert "provider-upload-secret" not in str(caught.value)


class TestAwsDocumentedSyntheticCreateFailures:
    def test_create_response_loss_is_ambiguous_and_redacted(self) -> None:
        class FailingCreateClient(RecordingS3Client):
            def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
                self.calls.append(("create_multipart_upload", kwargs))
                raise ReadTimeoutError(
                    endpoint_url="http://provider/?uploadId=provider-upload-secret"
                )

        storage, _, _ = _storage(internal=FailingCreateClient())

        with pytest.raises(MultipartInitiationAmbiguousError) as caught:
            storage.create_multipart("key")

        assert "provider-upload-secret" not in str(caught.value)

    def test_create_confirmed_rejection_is_not_ambiguity(self) -> None:
        class RejectedCreateClient(RecordingS3Client):
            def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
                self.calls.append(("create_multipart_upload", kwargs))
                raise _client_error("AccessDenied", 403, "CreateMultipartUpload")

        storage, _, _ = _storage(internal=RejectedCreateClient())

        with pytest.raises(ProviderOperationRejectedError):
            storage.create_multipart("key")

    def test_create_rejects_nonmapping_response(self) -> None:
        class MalformedCreateClient(RecordingS3Client):
            def create_multipart_upload(self, **kwargs: Any) -> Any:
                return ["not", "a", "mapping"]

        storage, _, _ = _storage(internal=MalformedCreateClient())

        with pytest.raises(ProviderContractError):
            storage.create_multipart("key")


class TestProviderContractDefensiveRedaction:
    def test_adapter_repr_does_not_expose_clients_bucket_or_credentials(self) -> None:
        class SecretClient(RecordingS3Client):
            def __repr__(self) -> str:
                return "access_key=private secret=private endpoint=http://private"

        storage = S3MultipartObjectStore(
            cast(Any, SecretClient()), cast(Any, SecretClient()), "private-bucket"
        )

        rendered = repr(storage)

        assert "access_key" not in rendered
        assert "secret" not in rendered
        assert "private-bucket" not in rendered

    def test_provider_failure_emits_no_secret_structured_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        internal = RecordingS3Client()
        internal.list_responses = [_client_error("NoSuchUpload", 404, "ListParts")]
        storage, _, _ = _storage(internal=internal)

        with caplog.at_level("DEBUG"), pytest.raises(MultipartSessionNotFoundError) as caught:
            storage.list_parts("private-object-key", ProviderUploadId("provider-upload-secret"))

        rendered = f"{caught.value!s}\n{caplog.text}"
        assert "provider-upload-secret" not in rendered
        assert "private-object-key" not in rendered
        assert "raw detail" not in rendered
