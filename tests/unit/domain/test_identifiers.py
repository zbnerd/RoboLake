"""Behavior tests for RoboLake identifiers."""

import pytest
from robolake.domain.errors import (
    InvalidDatasetName,
    InvalidDatasetReference,
    InvalidDigestError,
    UnsafePathError,
)
from robolake.domain.identifiers import (
    DatasetName,
    DatasetReference,
    RelativePath,
    Sha256Digest,
    object_key_for,
)


@pytest.mark.parametrize("raw", ["demo/pick-place", "로봇/session-01", "A/b_c-1.2"])
def test_dataset_name_accepts_normalized_names(raw: str) -> None:
    assert DatasetName.parse(raw).value == raw


@pytest.mark.parametrize(
    "raw",
    ["", "/demo", "demo/", "demo//x", "demo/../x", "a@v1", "bad\nname"],
)
def test_dataset_name_rejects_ambiguous_names(raw: str) -> None:
    with pytest.raises(InvalidDatasetName):
        DatasetName.parse(raw)


@pytest.mark.parametrize("raw", ["x" * 256, "é" * 128, "bad\ud800name"])
def test_dataset_name_rejects_overlong_or_non_scalar_text(raw: str) -> None:
    with pytest.raises(InvalidDatasetName):
        DatasetName.parse(raw)


def test_dataset_reference_splits_on_final_version_suffix() -> None:
    reference = DatasetReference.parse("demo/pick-place@v12")

    assert reference.dataset == DatasetName.parse("demo/pick-place")
    assert reference.version_number == 12


@pytest.mark.parametrize("raw", ["demo", "demo@v", "demo@v0", "demo@v-1", "demo@v1.0"])
def test_dataset_reference_rejects_invalid_suffix(raw: str) -> None:
    with pytest.raises(InvalidDatasetReference):
        DatasetReference.parse(raw)


def test_relative_path_normalizes_to_nfc_posix_text() -> None:
    assert RelativePath.parse("cafe\u0301/front.bin").value == "café/front.bin"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "/etc/passwd",
        "../escape",
        "a/../b",
        "a/./b",
        "a//b",
        "C:/robot/data",
        "//server/share",
        "a\\b",
        "a\0b",
        "a\nb",
        "\ud800/file",
        "a/" + "x" * 256,
        "/".join(["segment"] * 129),
    ],
)
def test_relative_path_rejects_unsafe_forms(raw: str) -> None:
    with pytest.raises(UnsafePathError):
        RelativePath.parse(raw)


def test_sha256_digest_exposes_raw_and_base64_forms() -> None:
    digest = Sha256Digest.parse("00" * 32)

    assert digest.raw_bytes == bytes(32)
    assert digest.checksum_base64 == "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.mark.parametrize("raw", ["", "0" * 63, "0" * 65, "G" * 64, "A" * 64])
def test_sha256_digest_rejects_noncanonical_values(raw: str) -> None:
    with pytest.raises(InvalidDigestError):
        Sha256Digest.parse(raw)


def test_object_key_is_derived_only_from_digest() -> None:
    digest = Sha256Digest.parse("abcdef" + "0" * 58)

    assert object_key_for(digest) == f"blobs/sha256/ab/cd/{digest.value}"
