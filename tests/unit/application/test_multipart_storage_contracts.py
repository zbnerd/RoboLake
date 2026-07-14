from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
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
from robolake.domain.errors import ProviderContractError
from robolake.domain.identifiers import Sha256Digest

pytestmark = pytest.mark.unit


def test_provider_upload_id_and_part_capability_repr_are_secret_safe() -> None:
    upload_id = ProviderUploadId("opaque-provider-upload-id-secret")
    capability = UploadPartCapability(
        part_number=7,
        size_bytes=6 * 1024 * 1024,
        expected_sha256=Sha256Digest.parse("a" * 64),
        request=PresignedRequest(
            "http://storage.invalid/bucket/key?uploadId=secret&X-Amz-Signature=secret",
            {
                "Content-Length": str(6 * 1024 * 1024),
                "x-amz-checksum-sha256": Sha256Digest.parse("a" * 64).checksum_base64,
            },
        ),
        expires_at=datetime(2026, 7, 14, tzinfo=UTC),
    )

    rendered = f"{upload_id!r} {upload_id!s} {capability!r}"

    assert "opaque-provider-upload-id-secret" not in rendered
    assert "uploadId" not in rendered
    assert "X-Amz" not in rendered
    assert "Signature" not in rendered


@pytest.mark.parametrize("raw", ["", "\x00", "line\nbreak", cast(Any, 123)])
def test_provider_upload_id_rejects_missing_or_control_text(raw: str) -> None:
    with pytest.raises(ProviderContractError):
        ProviderUploadId(raw)


def test_listed_provider_parts_are_observations_and_allow_equal_receipts() -> None:
    checksum = Sha256Digest.parse("b" * 64).checksum_base64
    observed_at = datetime(2026, 7, 14, tzinfo=UTC)

    first = ListedProviderPart(1, 6 * 1024 * 1024, '"same"', checksum, observed_at)
    second = ListedProviderPart(2, 6 * 1024 * 1024, '"same"', checksum, observed_at)

    assert first.etag == second.etag
    assert first.checksum_sha256_base64 == second.checksum_sha256_base64


@pytest.mark.parametrize(
    "part",
    [
        (0, 1, '"etag"'),
        (10_001, 1, '"etag"'),
        (1, 0, '"etag"'),
        (1, 1, ""),
    ],
)
def test_listed_provider_part_rejects_invalid_provider_shape(
    part: tuple[int, int, str],
) -> None:
    number, size, etag = part
    with pytest.raises(ProviderContractError):
        ListedProviderPart(
            number,
            size,
            etag,
            Sha256Digest.parse("c" * 64).checksum_base64,
            None,
        )


@pytest.mark.parametrize(
    ("etag", "checksum"),
    [
        (cast(Any, 123), Sha256Digest.parse("c" * 64).checksum_base64),
        ('"etag"', cast(Any, 123)),
    ],
)
def test_listed_provider_part_rejects_non_string_receipt_fields(etag: str, checksum: str) -> None:
    with pytest.raises(ProviderContractError):
        ListedProviderPart(1, 1, etag, checksum, None)


def test_completion_and_abort_results_use_closed_provider_neutral_outcomes() -> None:
    assert {item.value for item in MultipartCompletionOutcome} == {
        "COMPLETED",
        "PRECONDITION_LOST",
        "CONDITIONAL_CONFLICT",
    }
    assert {item.value for item in MultipartAbortOutcome} == {"ABORTED", "ALREADY_ABSENT"}
    assert (
        MultipartCompletionResult(MultipartCompletionOutcome.COMPLETED).outcome.value == "COMPLETED"
    )
    assert MultipartAbortResult(MultipartAbortOutcome.ABORTED).outcome.value == "ABORTED"


def test_final_object_inspection_keeps_provider_checksum_noncanonical() -> None:
    inspection = FinalObjectInspection(
        exists=True,
        size_bytes=12,
        etag='"multipart-2"',
        provider_checksum_sha256="opaque-composite-value-2",
        provider_checksum_type="COMPOSITE",
    )

    assert inspection.provider_checksum_sha256 == "opaque-composite-value-2"
    assert not hasattr(inspection, "sha256")


def test_missing_final_object_cannot_carry_provider_metadata() -> None:
    with pytest.raises(ProviderContractError):
        FinalObjectInspection(
            exists=False,
            size_bytes=1,
            etag=None,
            provider_checksum_sha256=None,
            provider_checksum_type=None,
        )
