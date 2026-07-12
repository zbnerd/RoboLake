"""Private staging and whole-tree atomic publication tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from robolake.application.contracts import DownloadItem, PresignedRequest
from robolake.domain.errors import OutputExistsError, StoredObjectMismatchError, UnsafePathError
from robolake.domain.identifiers import RelativePath, Sha256Digest
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.infrastructure.atomic_tree import AtomicTreeDownloader
from robolake.infrastructure.http_transfer import HttpByteTransfer


def _entry(path: str, data: bytes) -> ManifestEntry:
    return ManifestEntry(
        RelativePath.parse(path),
        len(data),
        Sha256Digest.parse(hashlib.sha256(data).hexdigest()),
    )


def _tree(data_by_path: dict[str, bytes]) -> AtomicTreeDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.removeprefix("/")
        return httpx.Response(200, content=data_by_path[name])

    transfer = HttpByteTransfer(httpx.Client(transport=httpx.MockTransport(handler)), chunk_size=2)
    return AtomicTreeDownloader(transfer)


def test_tree_reconstructs_nested_bytes_and_publishes_whole_directory(tmp_path: Path) -> None:
    data = {"a": b"abc", "b": b"de"}
    entries = (_entry("a.bin", data["a"]), _entry("nested/b.bin", data["b"]))
    manifest = Manifest.build(entries)
    output = tmp_path / "restored"
    tree = _tree(data)

    tree.begin(output, manifest)
    for name, entry in zip(data, manifest.entries, strict=True):
        prepared = tree.prepare(entry)
        tree.write(prepared, DownloadItem(entry, PresignedRequest(f"http://storage/{name}", {})))
    published = tree.commit()

    assert published == output
    assert (output / "a.bin").read_bytes() == b"abc"
    assert (output / "nested/b.bin").read_bytes() == b"de"
    assert not list(tmp_path.glob(".robolake-*.tmp"))


def test_tree_refuses_existing_output_and_symlinked_parent_before_staging(tmp_path: Path) -> None:
    manifest = Manifest.build([])
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(OutputExistsError):
        _tree({}).begin(output, manifest)

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(UnsafePathError):
        _tree({}).begin(linked_parent / "output", manifest)
    assert not list(tmp_path.glob(".robolake-*.tmp"))


def test_corrupt_download_aborts_private_staging_without_final_output(tmp_path: Path) -> None:
    expected = b"expected"
    manifest = Manifest.build([_entry("data.bin", expected)])
    output = tmp_path / "restored"
    tree = _tree({"wrong": b"corrupt!"})
    tree.begin(output, manifest)
    prepared = tree.prepare(manifest.entries[0])

    with pytest.raises(StoredObjectMismatchError):
        tree.write(
            prepared,
            DownloadItem(manifest.entries[0], PresignedRequest("http://storage/wrong", {})),
        )
    tree.abort()

    assert not output.exists()
    assert not list(tmp_path.glob(".robolake-*.tmp"))


def test_empty_tree_publishes_empty_output_and_race_never_replaces_winner(tmp_path: Path) -> None:
    manifest = Manifest.build([])
    output = tmp_path / "empty"
    tree = _tree({})
    tree.begin(output, manifest)
    assert tree.commit() == output
    assert output.is_dir()
    assert list(output.iterdir()) == []

    raced_output = tmp_path / "race"
    raced = _tree({})
    raced.begin(raced_output, manifest)
    raced_output.mkdir()
    (raced_output / "winner").write_text("keep", encoding="utf-8")
    with pytest.raises(OutputExistsError):
        raced.commit()
    raced.abort()
    assert (raced_output / "winner").read_text(encoding="utf-8") == "keep"
