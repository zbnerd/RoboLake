"""Behavior tests for safe local Dataset scanning."""

import io
import os
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

import pytest
from robolake.domain.errors import (
    SourceChangedError,
    UnsafeFileTypeError,
    UnsupportedFileSizeError,
    UnsupportedPlatformError,
)
from robolake.infrastructure import scanner as scanner_module
from robolake.infrastructure.scanner import FileSystemScanner, iter_file_chunks

pytestmark = pytest.mark.unit


def test_scanner_builds_manifest_without_storing_absolute_root(tmp_path: Path) -> None:
    root = tmp_path / "source"
    file_path = root / "camera" / "front.bin"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"synthetic-camera")

    scanned = FileSystemScanner().scan(root)

    assert scanned.manifest.file_count == 1
    assert scanned.manifest.entries[0].relative_path.value == "camera/front.bin"
    assert scanned.manifest.entries[0].size_bytes == len(b"synthetic-camera")
    assert scanned.files[0].local_ref.root.is_absolute()
    assert str(root.resolve()).encode() not in scanned.manifest.canonical_bytes


def test_scanner_manifest_is_deterministic_across_creation_order(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    for root, names in (
        (first_root, ("z.bin", "nested/a.bin")),
        (second_root, ("nested/a.bin", "z.bin")),
    ):
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())

    first = FileSystemScanner().scan(first_root)
    second = FileSystemScanner().scan(second_root)

    assert first.manifest.canonical_bytes == second.manifest.canonical_bytes
    assert first.manifest.sha256 == second.manifest.sha256


def test_scanner_accepts_empty_root_and_ignores_nested_empty_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "nested" / "empty").mkdir(parents=True)

    scanned = FileSystemScanner().scan(source)

    assert scanned.manifest.file_count == 0
    assert scanned.files == ()


@pytest.mark.parametrize("kind", ["root", "file", "directory"])
def test_scanner_rejects_symlinks_instead_of_following_or_skipping(
    tmp_path: Path, kind: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "bytes.bin").write_bytes(b"outside")

    source = tmp_path / "source"
    if kind == "root":
        source.symlink_to(outside, target_is_directory=True)
    else:
        source.mkdir()
        target = outside / "bytes.bin" if kind == "file" else outside
        (source / "link").symlink_to(target, target_is_directory=kind == "directory")

    with pytest.raises(UnsafeFileTypeError):
        FileSystemScanner().scan(source)


def test_scanner_rejects_fifo_before_opening_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    fifo = source / "stream.pipe"
    os.mkfifo(fifo)
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object) -> object:
        if path == fifo:
            raise AssertionError("scanner attempted to open a FIFO")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    with pytest.raises(UnsafeFileTypeError):
        FileSystemScanner().scan(source)


def test_scanner_rejects_oversized_file_using_only_logical_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "large.bin").write_bytes(b"12345678")

    with pytest.raises(UnsupportedFileSizeError) as captured:
        FileSystemScanner(max_single_put_bytes=7).scan(source)

    assert "large.bin" in str(captured.value)
    assert str(tmp_path) not in str(captured.value)


def test_scanner_rejects_file_changed_while_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    file_path = source / "changing.bin"
    file_path.write_bytes(b"original")
    original_iter = scanner_module.iter_file_chunks

    def mutating_chunks(stream: BinaryIO, chunk_size: int) -> Iterator[bytes]:
        for chunk in original_iter(stream, chunk_size):
            os.utime(file_path, ns=(file_path.stat().st_atime_ns, file_path.stat().st_mtime_ns + 1))
            yield chunk

    monkeypatch.setattr(scanner_module, "iter_file_chunks", mutating_chunks)

    with pytest.raises(SourceChangedError):
        FileSystemScanner().scan(source)


def test_file_chunks_never_request_an_unbounded_read() -> None:
    class GuardedStream(io.BytesIO):
        def read(self, size: int = -1, /) -> bytes:
            assert size == 4
            return super().read(size)

    assert list(iter_file_chunks(GuardedStream(b"123456789"), 4)) == [b"1234", b"5678", b"9"]


def test_scanner_does_not_follow_directory_swapped_to_outside_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    child = source / "child"
    child.mkdir(parents=True)
    (child / "inside.bin").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.bin").write_bytes(b"outside")
    original_open = os.open
    swapped = False

    def swapping_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == "child" and dir_fd is not None and flags & os.O_DIRECTORY and not swapped:
            child.rename(source / "original-child")
            child.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(UnsafeFileTypeError):
        FileSystemScanner().scan(source)


def test_scanner_rejects_unsupported_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(scanner_module.sys, "platform", "win32")

    with pytest.raises(UnsupportedPlatformError):
        FileSystemScanner().scan(source)
