"""Canonical immutable Dataset manifest."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import pairwise

from robolake.domain.constants import MAX_MANIFEST_BYTES, MAX_MANIFEST_ENTRIES
from robolake.domain.errors import ContentConflictError, ManifestMismatchError, PathCollisionError
from robolake.domain.identifiers import RelativePath, Sha256Digest


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One logical regular file in a Dataset snapshot."""

    relative_path: RelativePath
    size_bytes: int
    sha256: Sha256Digest

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ManifestMismatchError("Manifest entry size must be nonnegative.")


@dataclass(frozen=True, slots=True)
class Manifest:
    """Canonical ordered snapshot of regular files."""

    entries: tuple[ManifestEntry, ...]
    canonical_bytes: bytes = field(repr=False)
    sha256: Sha256Digest
    schema_version: int = 1

    @property
    def file_count(self) -> int:
        """Number of logical regular-file entries."""
        return len(self.entries)

    @property
    def logical_bytes(self) -> int:
        """Total reconstructed bytes, counting each logical path."""
        return sum(entry.size_bytes for entry in self.entries)

    @property
    def unique_blob_count(self) -> int:
        """Number of distinct content digests."""
        return len({entry.sha256 for entry in self.entries})

    @property
    def unique_blob_bytes(self) -> int:
        """Total bytes in the deduplicated content set."""
        return sum({entry.sha256: entry.size_bytes for entry in self.entries}.values())

    @classmethod
    def build(cls, entries: Iterable[ManifestEntry]) -> Manifest:
        """Build canonical bytes independent of input traversal order."""
        supplied = tuple(entries)
        if len(supplied) > MAX_MANIFEST_ENTRIES:
            raise ManifestMismatchError("Manifest exceeds the protocol entry limit.")
        ordered = tuple(
            sorted(supplied, key=lambda entry: entry.relative_path.value.encode("utf-8"))
        )
        digest_sizes: dict[Sha256Digest, int] = {}
        for entry in ordered:
            existing_size = digest_sizes.setdefault(entry.sha256, entry.size_bytes)
            if existing_size != entry.size_bytes:
                raise ContentConflictError("One SHA-256 digest has conflicting sizes.")
        collision_keys = sorted(
            unicodedata.normalize("NFC", entry.relative_path.value.casefold()) for entry in ordered
        )
        for previous, current in pairwise(collision_keys):
            if current == previous or current.startswith(f"{previous}/"):
                raise PathCollisionError("Manifest contains colliding logical paths.")
        payload = {
            "schema_version": 1,
            "entries": [
                {
                    "relative_path": entry.relative_path.value,
                    "size_bytes": entry.size_bytes,
                    "sha256": entry.sha256.value,
                }
                for entry in ordered
            ],
        }
        canonical_bytes = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(canonical_bytes) > MAX_MANIFEST_BYTES:
            raise ManifestMismatchError("Manifest exceeds the protocol byte limit.")
        digest = Sha256Digest.parse(hashlib.sha256(canonical_bytes).hexdigest())
        return cls(entries=ordered, canonical_bytes=canonical_bytes, sha256=digest)

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> Manifest:
        """Parse and independently reproduce a canonical manifest."""
        if len(data) > MAX_MANIFEST_BYTES:
            raise ManifestMismatchError("Manifest exceeds the protocol byte limit.")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ManifestMismatchError("Manifest must be canonical UTF-8 JSON.") from error
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "entries"}:
            raise ManifestMismatchError("Manifest must contain only schema_version and entries.")
        if payload["schema_version"] != 1 or not isinstance(payload["entries"], list):
            raise ManifestMismatchError("Manifest schema version or entries are invalid.")

        for item in payload["entries"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"relative_path", "size_bytes", "sha256"}
                or not isinstance(item["relative_path"], str)
                or not isinstance(item["size_bytes"], int)
                or isinstance(item["size_bytes"], bool)
                or not isinstance(item["sha256"], str)
            ):
                raise ManifestMismatchError("Manifest entry fields are invalid.")
        entries = [
            ManifestEntry(
                relative_path=RelativePath.parse(item["relative_path"]),
                size_bytes=item["size_bytes"],
                sha256=Sha256Digest.parse(item["sha256"]),
            )
            for item in payload["entries"]
        ]
        rebuilt = cls.build(entries)
        if rebuilt.canonical_bytes != data:
            raise ManifestMismatchError("Manifest bytes are not canonical schema version 1.")
        return rebuilt
