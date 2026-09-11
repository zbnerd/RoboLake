"""Descriptor-relative local regular-file Dataset scanner."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from robolake.application.contracts import (
    FileIdentity,
    LocalFileRef,
    ScannedDataset,
    ScannedFile,
)
from robolake.domain.errors import (
    SourceChangedError,
    UnsafeFileTypeError,
    UnsupportedFileSizeError,
    UnsupportedPlatformError,
)
from robolake.domain.identifiers import RelativePath, Sha256Digest
from robolake.domain.manifest import Manifest, ManifestEntry


def _identity(stat_result: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size_bytes=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
    )


def iter_file_chunks(stream: BinaryIO, chunk_size: int) -> Iterator[bytes]:
    """Read a file using one bounded chunk size."""
    while chunk := stream.read(chunk_size):
        yield chunk


class FileSystemScanner:
    """Build a canonical manifest without following source-tree links."""

    def __init__(
        self,
        *,
        max_single_put_bytes: int = 5_000_000_000,
        chunk_size: int = 1_048_576,
    ) -> None:
        self._max_single_put_bytes = max_single_put_bytes
        self._chunk_size = chunk_size

    def scan(self, source: Path) -> ScannedDataset:
        """Scan stable regular files beneath source using directory descriptors."""
        self._require_supported_platform()
        source_stat = source.lstat()
        if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISDIR(source_stat.st_mode):
            raise UnsafeFileTypeError("Dataset source root must be a non-symlink directory.")

        root = source.absolute()
        root_fd = self._open_directory(os.fspath(source), dir_fd=None)
        try:
            root_identity = _identity(os.fstat(root_fd))
            if root_identity != _identity(source_stat):
                raise SourceChangedError("source root")
            scanned_files: list[ScannedFile] = []
            self._scan_directory(
                directory_fd=root_fd,
                raw_parent_parts=(),
                root=root,
                root_identity=root_identity,
                output=scanned_files,
            )
        finally:
            os.close(root_fd)

        manifest = Manifest.build(item.entry for item in scanned_files)
        by_path = {item.entry.relative_path: item for item in scanned_files}
        ordered_files = tuple(by_path[entry.relative_path] for entry in manifest.entries)
        return ScannedDataset(root=root, manifest=manifest, files=ordered_files)

    def _scan_directory(
        self,
        *,
        directory_fd: int,
        raw_parent_parts: tuple[str, ...],
        root: Path,
        root_identity: FileIdentity,
        output: list[ScannedFile],
    ) -> None:
        with os.scandir(directory_fd) as entries:
            for directory_entry in entries:
                raw_parts = (*raw_parent_parts, directory_entry.name)
                try:
                    discovered = directory_entry.stat(follow_symlinks=False)
                except FileNotFoundError as error:
                    raise SourceChangedError("/".join(raw_parts)) from error

                if stat.S_ISLNK(discovered.st_mode):
                    raise UnsafeFileTypeError("Dataset source contains a symlink.")
                if stat.S_ISDIR(discovered.st_mode):
                    child_fd = self._open_directory(directory_entry.name, dir_fd=directory_fd)
                    try:
                        self._scan_directory(
                            directory_fd=child_fd,
                            raw_parent_parts=raw_parts,
                            root=root,
                            root_identity=root_identity,
                            output=output,
                        )
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(discovered.st_mode):
                    raise UnsafeFileTypeError("Dataset source contains a non-regular file.")
                output.append(
                    self._scan_file(
                        parent_fd=directory_fd,
                        raw_parts=raw_parts,
                        root=root,
                        root_identity=root_identity,
                    )
                )

    def _scan_file(
        self,
        *,
        parent_fd: int,
        raw_parts: tuple[str, ...],
        root: Path,
        root_identity: FileIdentity,
    ) -> ScannedFile:
        relative = RelativePath.parse("/".join(raw_parts))
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open(raw_parts[-1], flags, dir_fd=parent_fd)
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                raise UnsafeFileTypeError(
                    "Dataset source file changed or became unsafe."
                ) from error
            raise
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise UnsafeFileTypeError("Dataset source contains a non-regular file.")
            if before.st_size > self._max_single_put_bytes:
                raise UnsupportedFileSizeError(relative.value, before.st_size)

            digest = hashlib.sha256()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                for chunk in iter_file_chunks(stream, self._chunk_size):
                    digest.update(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)

        if _identity(after) != _identity(before):
            raise SourceChangedError(relative.value)
        entry = ManifestEntry(
            relative_path=relative,
            size_bytes=before.st_size,
            sha256=Sha256Digest.parse(digest.hexdigest()),
        )
        return ScannedFile(
            entry=entry,
            local_ref=LocalFileRef(
                root=root,
                root_identity=root_identity,
                raw_parts=raw_parts,
                file_identity=_identity(before),
            ),
        )

    @staticmethod
    def _open_directory(path: str, *, dir_fd: int | None) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            return os.open(path, flags, dir_fd=dir_fd)
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                raise UnsafeFileTypeError(
                    "Dataset source directory changed or became unsafe."
                ) from error
            raise

    @staticmethod
    def _require_supported_platform() -> None:
        required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
        if sys.platform not in {"linux", "darwin"} or any(
            not hasattr(os, name) for name in required
        ):
            raise UnsupportedPlatformError("M1 scanning requires supported Linux/macOS file APIs.")
