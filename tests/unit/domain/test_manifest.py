"""Behavior tests for the canonical Dataset manifest."""

from itertools import repeat

import pytest
from robolake.domain.constants import MAX_MANIFEST_BYTES, MAX_MANIFEST_ENTRIES
from robolake.domain.errors import ContentConflictError, ManifestMismatchError, PathCollisionError
from robolake.domain.identifiers import RelativePath, Sha256Digest
from robolake.domain.manifest import Manifest, ManifestEntry

EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
A_SHA = "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"


def test_manifest_has_golden_canonical_bytes_and_hash() -> None:
    manifest = Manifest.build(
        [
            ManifestEntry(RelativePath.parse("z.bin"), 0, Sha256Digest.parse(EMPTY_SHA)),
            ManifestEntry(RelativePath.parse("a.txt"), 1, Sha256Digest.parse(A_SHA)),
        ]
    )
    expected = (
        b'{"schema_version":1,"entries":['
        b'{"relative_path":"a.txt","size_bytes":1,'
        b'"sha256":"ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"},'
        b'{"relative_path":"z.bin","size_bytes":0,'
        b'"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}]}'
    )

    assert manifest.canonical_bytes == expected
    assert (
        manifest.sha256.value == "b4d7ea7eafa613e1bb473a87a3e9f4e9656235982f67a09cae158b683a6c52a3"
    )


def test_empty_manifest_is_canonical() -> None:
    manifest = Manifest.build([])

    assert manifest.canonical_bytes == b'{"schema_version":1,"entries":[]}'
    assert (
        manifest.sha256.value == "ba4fc47c6525d8de1a187fe7d41e1de1b2fe52df1f6a43d0544675d3ac44e5fe"
    )
    assert manifest.file_count == 0
    assert manifest.logical_bytes == 0
    assert manifest.unique_blob_count == 0
    assert manifest.unique_blob_bytes == 0


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("camera.bin", "camera.bin"),
        ("Camera/A.bin", "camera/a.bin"),
        ("Foo", "foo/bar.bin"),
    ],
)
def test_manifest_rejects_nonportable_path_collisions(first: str, second: str) -> None:
    entries = [
        ManifestEntry(RelativePath.parse(first), 0, Sha256Digest.parse(EMPTY_SHA)),
        ManifestEntry(RelativePath.parse(second), 0, Sha256Digest.parse(EMPTY_SHA)),
    ]

    with pytest.raises(PathCollisionError):
        Manifest.build(entries)


def test_manifest_entry_rejects_negative_size() -> None:
    with pytest.raises(ManifestMismatchError):
        ManifestEntry(RelativePath.parse("negative.bin"), -1, Sha256Digest.parse(EMPTY_SHA))


def test_manifest_rejects_one_digest_with_different_sizes() -> None:
    entries = [
        ManifestEntry(RelativePath.parse("a.bin"), 1, Sha256Digest.parse(A_SHA)),
        ManifestEntry(RelativePath.parse("b.bin"), 2, Sha256Digest.parse(A_SHA)),
    ]

    with pytest.raises(ContentConflictError):
        Manifest.build(entries)


def test_manifest_separates_logical_and_unique_content_totals() -> None:
    manifest = Manifest.build(
        [
            ManifestEntry(RelativePath.parse("a.bin"), 1, Sha256Digest.parse(A_SHA)),
            ManifestEntry(RelativePath.parse("b.bin"), 1, Sha256Digest.parse(A_SHA)),
        ]
    )

    assert manifest.file_count == 2
    assert manifest.logical_bytes == 2
    assert manifest.unique_blob_count == 1
    assert manifest.unique_blob_bytes == 1


def test_manifest_rejects_more_than_protocol_entry_limit() -> None:
    digest = Sha256Digest.parse(EMPTY_SHA)
    entry = ManifestEntry(RelativePath.parse("repeated.bin"), 0, digest)

    with pytest.raises(ManifestMismatchError):
        Manifest.build(repeat(entry, MAX_MANIFEST_ENTRIES + 1))


def test_manifest_round_trips_only_through_canonical_bytes() -> None:
    original = Manifest.build(
        [ManifestEntry(RelativePath.parse("a.txt"), 1, Sha256Digest.parse(A_SHA))]
    )

    restored = Manifest.from_canonical_bytes(original.canonical_bytes)

    assert restored == original


@pytest.mark.parametrize(
    "data",
    [
        b'{"schema_version":1, "entries":[]}',
        b'{"schema_version":2,"entries":[]}',
        (
            b'{"schema_version":1,"entries":[{"relative_path":"a","size_bytes":0,'
            b'"sha256":"' + EMPTY_SHA.encode() + b'","media_type":"text/plain"}]}'
        ),
        b'{"entries":[]}',
        b"[]",
        b"not-json",
        b"\xff",
    ],
)
def test_manifest_rejects_untrusted_noncanonical_bytes(data: bytes) -> None:
    with pytest.raises(ManifestMismatchError):
        Manifest.from_canonical_bytes(data)


def test_manifest_rejects_bytes_above_protocol_limit_before_parsing() -> None:
    with pytest.raises(ManifestMismatchError):
        Manifest.from_canonical_bytes(b" " * (MAX_MANIFEST_BYTES + 1))


def test_manifest_build_enforces_canonical_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("robolake.domain.manifest.MAX_MANIFEST_BYTES", 1)

    with pytest.raises(ManifestMismatchError):
        Manifest.build([])
