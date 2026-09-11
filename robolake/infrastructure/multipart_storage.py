"""S3-compatible multipart provider adapter with sanitized boundaries."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from botocore.exceptions import BotoCoreError, ClientError

from robolake.application.contracts import (
    FinalObjectInspection,
    ListedProviderPart,
    MultipartAbortOutcome,
    MultipartAbortResult,
    MultipartCompletionOutcome,
    MultipartCompletionResult,
    PresignedRequest,
    ProviderUploadId,
    UploadPartCapability,
)
from robolake.domain.errors import (
    ContentConflictError,
    MultipartAbortAmbiguousError,
    MultipartCompletionAmbiguousError,
    MultipartInitiationAmbiguousError,
    MultipartSessionNotFoundError,
    PartChecksumRejectedError,
    ProviderContractError,
    ProviderOperationRejectedError,
    TransientProviderFailureError,
)
from robolake.domain.identifiers import Sha256Digest
from robolake.domain.multipart import CompletedPartReceipt, sha256_base64_to_digest

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import CompletedPartTypeDef


@dataclass(frozen=True, slots=True)
class S3MultipartObjectStore:
    """Multipart control adapter using internal and caller-reachable S3 clients."""

    internal_client: S3Client = field(repr=False)
    public_client: S3Client = field(repr=False)
    bucket: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.bucket:
            raise ProviderContractError("Multipart object store requires a bucket.")

    def create_multipart(self, object_key: str) -> ProviderUploadId:
        """Create one checksum-enabled provider attempt at the deterministic final key."""
        try:
            response = self.internal_client.create_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                ChecksumAlgorithm="SHA256",
            )
        except ClientError as error:
            if (_provider_status(error) or 0) >= 500:
                raise MultipartInitiationAmbiguousError(
                    _safe_message("create_multipart", object_key)
                ) from None
            raise ProviderOperationRejectedError(
                _safe_message("create_multipart", object_key)
            ) from None
        except BotoCoreError:
            raise MultipartInitiationAmbiguousError(
                _safe_message("create_multipart", object_key)
            ) from None
        if not isinstance(response, Mapping):
            raise ProviderContractError(_safe_message("create_multipart", object_key))
        upload_id = response.get("UploadId")
        if not isinstance(upload_id, str):
            raise ProviderContractError(_safe_message("create_multipart", object_key))
        return ProviderUploadId(upload_id)

    def presign_upload_part(
        self,
        object_key: str,
        upload_id: ProviderUploadId,
        part_number: int,
        size_bytes: int,
        checksum_sha256_base64: str,
        expires_seconds: int,
    ) -> UploadPartCapability:
        """Issue one exact size/checksum/part-bound UploadPart capability."""
        if (
            type(part_number) is not int
            or not 1 <= part_number <= 10_000
            or type(size_bytes) is not int
            or size_bytes <= 0
            or type(expires_seconds) is not int
            or expires_seconds <= 0
        ):
            raise ProviderContractError("UploadPart capability request is invalid.")
        try:
            expected_sha256 = sha256_base64_to_digest(checksum_sha256_base64)
            url = self.public_client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self.bucket,
                    "Key": object_key,
                    "UploadId": upload_id.value,
                    "PartNumber": part_number,
                    "ContentLength": size_bytes,
                    "ChecksumSHA256": checksum_sha256_base64,
                },
                ExpiresIn=expires_seconds,
                HttpMethod="PUT",
            )
        except PartChecksumRejectedError as error:
            raise ProviderContractError("UploadPart checksum is invalid.") from error
        except BotoCoreError:
            raise ProviderContractError(_safe_message("presign_upload_part", object_key)) from None
        if not _is_safe_capability_url(url):
            raise ProviderContractError(_safe_message("presign_upload_part", object_key))
        return UploadPartCapability(
            part_number=part_number,
            size_bytes=size_bytes,
            expected_sha256=expected_sha256,
            request=PresignedRequest(
                url=url,
                headers={
                    "Content-Length": str(size_bytes),
                    "x-amz-checksum-sha256": checksum_sha256_base64,
                },
            ),
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_seconds),
        )

    def list_parts(
        self, object_key: str, upload_id: ProviderUploadId
    ) -> tuple[ListedProviderPart, ...]:
        """Read and validate every provider page without manufacturing response receipts."""
        marker: int | None = None
        seen_markers: set[int] = set()
        observed: dict[int, ListedProviderPart] = {}
        while True:
            request: dict[str, Any] = {
                "Bucket": self.bucket,
                "Key": object_key,
                "UploadId": upload_id.value,
            }
            if marker is not None:
                request["PartNumberMarker"] = marker
            try:
                response = self.internal_client.list_parts(**request)
            except ClientError as error:
                if _provider_code(error) == "NoSuchUpload":
                    raise MultipartSessionNotFoundError(
                        _safe_message("list_parts", object_key)
                    ) from None
                raise _client_failure("list_parts", object_key, error) from None
            except BotoCoreError:
                raise TransientProviderFailureError(
                    _safe_message("list_parts", object_key)
                ) from None
            if not isinstance(response, Mapping):
                raise ProviderContractError(_safe_message("list_parts", object_key))
            parts = response.get("Parts", [])
            truncated = response.get("IsTruncated")
            if not isinstance(parts, list) or type(truncated) is not bool:
                raise ProviderContractError(_safe_message("list_parts", object_key))
            for raw_part in parts:
                part = _parse_listed_part(raw_part, object_key)
                if part.part_number in observed:
                    raise ProviderContractError(_safe_message("list_parts", object_key))
                observed[part.part_number] = part
            if not truncated:
                break
            next_marker = response.get("NextPartNumberMarker")
            if (
                type(next_marker) is not int
                or next_marker <= 0
                or next_marker in seen_markers
                or (marker is not None and next_marker <= marker)
            ):
                raise ProviderContractError(_safe_message("list_parts", object_key))
            seen_markers.add(next_marker)
            marker = next_marker
        return tuple(observed[number] for number in sorted(observed))

    def inspect_final_object(self, object_key: str) -> FinalObjectInspection:
        """Read provider-owned final-key facts without asserting canonical Blob identity."""
        try:
            response = self.internal_client.head_object(
                Bucket=self.bucket,
                Key=object_key,
                ChecksumMode="ENABLED",
            )
        except ClientError as error:
            if _provider_code(error) in {"404", "NoSuchKey", "NotFound"}:
                return FinalObjectInspection(False, None, None, None, None)
            raise _client_failure("inspect_final_object", object_key, error) from None
        except BotoCoreError:
            raise TransientProviderFailureError(
                _safe_message("inspect_final_object", object_key)
            ) from None
        if not isinstance(response, Mapping):
            raise ProviderContractError(_safe_message("inspect_final_object", object_key))
        size = response.get("ContentLength")
        etag = response.get("ETag")
        checksum = response.get("ChecksumSHA256")
        checksum_type = response.get("ChecksumType")
        if (
            type(size) is not int
            or size < 0
            or not isinstance(etag, str)
            or (checksum is not None and not isinstance(checksum, str))
            or (checksum_type is not None and not isinstance(checksum_type, str))
        ):
            raise ProviderContractError(_safe_message("inspect_final_object", object_key))
        return FinalObjectInspection(True, size, etag, checksum, checksum_type)

    def complete_multipart(
        self,
        object_key: str,
        upload_id: ProviderUploadId,
        parts: Sequence[CompletedPartReceipt],
    ) -> MultipartCompletionResult:
        """Conditionally assemble one final object from response-backed receipts."""
        if any(not isinstance(part, CompletedPartReceipt) for part in parts):
            raise ProviderContractError(
                "CompleteMultipartUpload requires UploadPart response receipts."
            )
        ordered = tuple(sorted(parts, key=lambda item: item.part_number))
        if any(
            not isinstance(part.response_etag, str)
            or not part.response_etag
            or _has_control_character(part.response_etag)
            for part in ordered
        ):
            raise ProviderContractError("CompleteMultipartUpload receipt is invalid.")
        if not ordered or tuple(part.part_number for part in ordered) != tuple(
            range(1, len(ordered) + 1)
        ):
            raise ProviderContractError("CompleteMultipartUpload receipts must be consecutive.")
        provider_parts: list[CompletedPartTypeDef] = [
            {
                "PartNumber": part.part_number,
                "ETag": part.response_etag,
                "ChecksumSHA256": part.response_checksum_sha256_base64,
            }
            for part in ordered
        ]
        try:
            response = self.internal_client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                UploadId=upload_id.value,
                MultipartUpload={"Parts": provider_parts},
                IfNoneMatch="*",
            )
        except ClientError as error:
            code = _provider_code(error)
            status = _provider_status(error)
            if code == "NoSuchUpload":
                raise MultipartSessionNotFoundError(
                    _safe_message("complete_multipart", object_key)
                ) from None
            if status == 412 or code in {"PreconditionFailed", "ConditionalRequestFailed"}:
                return MultipartCompletionResult(MultipartCompletionOutcome.PRECONDITION_LOST)
            if status == 409 or code == "ConditionalRequestConflict":
                return MultipartCompletionResult(MultipartCompletionOutcome.CONDITIONAL_CONFLICT)
            if status == 200 or (status is not None and status >= 500):
                raise MultipartCompletionAmbiguousError(
                    _safe_message("complete_multipart", object_key)
                ) from None
            raise ProviderOperationRejectedError(
                _safe_message("complete_multipart", object_key)
            ) from None
        except BotoCoreError:
            raise MultipartCompletionAmbiguousError(
                _safe_message("complete_multipart", object_key)
            ) from None
        if not isinstance(response, Mapping) or not isinstance(response.get("ETag"), str):
            raise ProviderContractError(_safe_message("complete_multipart", object_key))
        metadata = response.get("ResponseMetadata")
        if not isinstance(metadata, Mapping) or metadata.get("HTTPStatusCode") != 200:
            raise ProviderContractError(_safe_message("complete_multipart", object_key))
        return MultipartCompletionResult(MultipartCompletionOutcome.COMPLETED)

    def abort_multipart(self, object_key: str, upload_id: ProviderUploadId) -> MultipartAbortResult:
        """Abort one known incomplete upload without touching a completed object."""
        try:
            response = self.internal_client.abort_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                UploadId=upload_id.value,
            )
        except ClientError as error:
            if _provider_code(error) == "NoSuchUpload":
                return MultipartAbortResult(MultipartAbortOutcome.ALREADY_ABSENT)
            status = _provider_status(error)
            if status is None or status >= 500:
                raise MultipartAbortAmbiguousError(
                    _safe_message("abort_multipart", object_key)
                ) from None
            raise ProviderOperationRejectedError(
                _safe_message("abort_multipart", object_key)
            ) from None
        except BotoCoreError:
            raise MultipartAbortAmbiguousError(
                _safe_message("abort_multipart", object_key)
            ) from None
        if not isinstance(response, Mapping):
            raise ProviderContractError(_safe_message("abort_multipart", object_key))
        metadata = response.get("ResponseMetadata")
        if not isinstance(metadata, Mapping) or metadata.get("HTTPStatusCode") not in {200, 204}:
            raise ProviderContractError(_safe_message("abort_multipart", object_key))
        return MultipartAbortResult(MultipartAbortOutcome.ABORTED)


def parse_upload_part_response(
    *,
    part_number: int,
    status_code: int,
    response: Mapping[str, object],
    expected_sha256: Sha256Digest,
) -> CompletedPartReceipt:
    """Build a completion receipt only from one successful UploadPart response."""
    if type(status_code) is not int or status_code != 200 or not isinstance(response, Mapping):
        raise ProviderContractError("UploadPart response is missing its required receipt.")
    etag = response.get("ETag")
    checksum = response.get("ChecksumSHA256")
    if (
        not isinstance(etag, str)
        or not etag
        or _has_control_character(etag)
        or not isinstance(checksum, str)
    ):
        raise ProviderContractError("UploadPart response is missing its required receipt.")
    try:
        return CompletedPartReceipt.from_upload_response(
            part_number,
            etag,
            checksum,
            expected_sha256,
        )
    except (ContentConflictError, PartChecksumRejectedError) as error:
        raise ProviderContractError("UploadPart response receipt is invalid.") from error


def _parse_listed_part(raw: object, object_key: str) -> ListedProviderPart:
    if not isinstance(raw, Mapping):
        raise ProviderContractError(_safe_message("list_parts", object_key))
    part_number = raw.get("PartNumber")
    size = raw.get("Size")
    etag = raw.get("ETag")
    checksum = raw.get("ChecksumSHA256")
    modified_at = raw.get("LastModified")
    if (
        type(part_number) is not int
        or type(size) is not int
        or not isinstance(etag, str)
        or not isinstance(checksum, str)
        or (modified_at is not None and not isinstance(modified_at, datetime))
    ):
        raise ProviderContractError(_safe_message("list_parts", object_key))
    try:
        return ListedProviderPart(part_number, size, etag, checksum, modified_at)
    except ProviderContractError as error:
        raise ProviderContractError(_safe_message("list_parts", object_key)) from error


def _provider_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _provider_status(error: ClientError) -> int | None:
    value = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return value if type(value) is int else None


def _client_failure(operation: str, object_key: str, error: ClientError) -> Exception:
    status = _provider_status(error)
    if status is not None and status >= 500:
        return TransientProviderFailureError(_safe_message(operation, object_key))
    return ProviderOperationRejectedError(_safe_message(operation, object_key))


def _safe_message(operation: str, object_key: str) -> str:
    fingerprint = hashlib.sha256(object_key.encode("utf-8")).hexdigest()[:12]
    return f"Object storage {operation} failed (object={fingerprint})."


def _has_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _is_safe_capability_url(value: object) -> bool:
    if not isinstance(value, str) or not value or _has_control_character(value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
