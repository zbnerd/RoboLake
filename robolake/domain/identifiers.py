"""Validated identifiers used by RoboLake domain behavior."""

from __future__ import annotations

import base64
import unicodedata
from dataclasses import dataclass

from robolake.domain.constants import (
    MAX_DATASET_NAME_BYTES,
    MAX_PATH_SEGMENT_BYTES,
    MAX_RELATIVE_PATH_BYTES,
)
from robolake.domain.errors import (
    InvalidDatasetName,
    InvalidDatasetReference,
    InvalidDigestError,
    UnsafePathError,
)


@dataclass(frozen=True, slots=True)
class DatasetName:
    """Normalized logical Dataset name."""

    value: str

    def __post_init__(self) -> None:
        normalized = unicodedata.normalize("NFC", self.value)
        segments = normalized.split("/")
        try:
            encoded = normalized.encode("utf-8")
        except UnicodeEncodeError as error:
            raise InvalidDatasetName(
                "Dataset name must contain valid Unicode scalar text."
            ) from error
        if (
            not normalized
            or len(encoded) > MAX_DATASET_NAME_BYTES
            or normalized.startswith("/")
            or normalized.endswith("/")
            or "@" in normalized
            or any(not segment or segment in {".", ".."} for segment in segments)
            or any(unicodedata.category(character) in {"Cc", "Cs"} for character in normalized)
        ):
            raise InvalidDatasetName("Dataset name must use safe, non-empty namespace segments.")
        object.__setattr__(self, "value", normalized)

    @classmethod
    def parse(cls, raw: str) -> DatasetName:
        """Parse a Dataset name through the same constructor invariants."""
        return cls(raw)


@dataclass(frozen=True, slots=True)
class DatasetReference:
    """Human Dataset reference using its registration-order version number."""

    dataset: DatasetName
    version_number: int

    def __str__(self) -> str:
        return f"{self.dataset.value}@v{self.version_number}"

    @classmethod
    def parse(cls, raw: str) -> DatasetReference:
        """Split a reference on its final version suffix."""
        name, marker, number = raw.rpartition("@v")
        if not marker or not number.isdecimal() or int(number) < 1:
            raise InvalidDatasetReference("Expected DATASET@v<positive integer>.")
        return cls(dataset=DatasetName.parse(name), version_number=int(number))


@dataclass(frozen=True, slots=True)
class RelativePath:
    """Normalized logical POSIX path relative to a Dataset root."""

    value: str

    def __post_init__(self) -> None:
        normalized = unicodedata.normalize("NFC", self.value)
        segments = normalized.split("/")
        try:
            encoded = normalized.encode("utf-8")
            encoded_segments = [segment.encode("utf-8") for segment in segments]
        except UnicodeEncodeError as error:
            raise UnsafePathError(
                "Relative path must contain valid Unicode scalar text."
            ) from error

        drive_qualified = bool(
            segments
            and len(segments[0]) == 2
            and segments[0][0].isalpha()
            and segments[0][1] == ":"
        )
        invalid = (
            not normalized
            or normalized.startswith("/")
            or "\\" in normalized
            or drive_qualified
            or len(encoded) > MAX_RELATIVE_PATH_BYTES
            or any(not segment or segment in {".", ".."} for segment in segments)
            or any(len(segment) > MAX_PATH_SEGMENT_BYTES for segment in encoded_segments)
            or any(unicodedata.category(character) in {"Cc", "Cs"} for character in normalized)
        )
        if invalid:
            raise UnsafePathError("Relative path must use safe POSIX segments.")
        object.__setattr__(self, "value", normalized)

    @classmethod
    def parse(cls, raw: str) -> RelativePath:
        """Parse a logical relative path."""
        return cls(raw)


@dataclass(frozen=True, slots=True)
class Sha256Digest:
    """Lowercase hexadecimal SHA-256 identity."""

    value: str

    def __post_init__(self) -> None:
        if len(self.value) != 64 or any(
            character not in "0123456789abcdef" for character in self.value
        ):
            raise InvalidDigestError("SHA-256 must be 64 lowercase hexadecimal characters.")

    @classmethod
    def parse(cls, raw: str) -> Sha256Digest:
        """Parse a SHA-256 digest."""
        return cls(raw)

    @property
    def raw_bytes(self) -> bytes:
        """Return the raw 32-byte digest."""
        return bytes.fromhex(self.value)

    @property
    def checksum_base64(self) -> str:
        """Return the S3 checksum header representation."""
        return base64.b64encode(self.raw_bytes).decode("ascii")


def object_key_for(digest: Sha256Digest) -> str:
    """Derive the immutable physical key for a Blob digest."""
    return f"blobs/sha256/{digest.value[:2]}/{digest.value[2:4]}/{digest.value}"
