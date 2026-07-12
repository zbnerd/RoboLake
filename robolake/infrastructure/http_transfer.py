"""Direct streamed object transfer without permanent storage credentials."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx

from robolake.application.contracts import (
    DownloadItem,
    FileIdentity,
    LocalFileRef,
    PresignedRequest,
    PutOutcome,
    PutReceipt,
)
from robolake.domain.errors import (
    RetryableTransferError,
    SourceChangedError,
    StoredObjectMismatchError,
    TransferRejectedError,
    UnsafeFileTypeError,
)
from robolake.domain.manifest import ManifestEntry


def _identity(value: os.stat_result) -> FileIdentity:
    return FileIdentity(value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


@dataclass(slots=True)
class _StreamState:
    size_bytes: int = 0
    sha256: str | None = None
    complete: bool = False


class HttpByteTransfer:
    """Reopen a scanned file safely and stream it to a presigned request."""

    def __init__(self, client: httpx.Client, *, chunk_size: int = 1_048_576) -> None:
        self._client = client
        self._chunk_size = chunk_size

    def put(
        self,
        local_ref: LocalFileRef,
        request: PresignedRequest,
        expected_entry: ManifestEntry,
    ) -> PutReceipt:
        """Classify only 200/412/409 while keeping provider details private."""
        with _open_scanned_file(local_ref, expected_entry) as descriptor:
            state = _StreamState()
            content = self._iter_verified(descriptor, local_ref, expected_entry, state)
            try:
                response = self._client.put(
                    request.url,
                    headers=dict(request.headers),
                    content=content,
                )
            except httpx.RequestError as error:
                raise RetryableTransferError(
                    f"Transfer failed for {expected_entry.relative_path.value}; retry push."
                ) from error

            if response.status_code == 200:
                if (
                    not state.complete
                    or state.size_bytes != expected_entry.size_bytes
                    or state.sha256 != expected_entry.sha256.value
                ):
                    raise SourceChangedError(expected_entry.relative_path.value)
                return PutReceipt(PutOutcome.CREATED, response.headers.get("ETag"))
            if response.status_code == 412:
                return PutReceipt(PutOutcome.ALREADY_EXISTS, response.headers.get("ETag"))
            if response.status_code == 409:
                return PutReceipt(PutOutcome.CONFLICT, response.headers.get("ETag"))
            if response.status_code in {408, 429} or response.status_code >= 500:
                raise RetryableTransferError(
                    f"Storage returned retryable status {response.status_code} for "
                    f"{expected_entry.relative_path.value}."
                )
            raise TransferRejectedError(
                f"Storage rejected {expected_entry.relative_path.value} with status "
                f"{response.status_code}."
            )

    def _iter_verified(
        self,
        descriptor: int,
        local_ref: LocalFileRef,
        expected_entry: ManifestEntry,
        state: _StreamState,
    ) -> Iterator[bytes]:
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, self._chunk_size):
            state.size_bytes += len(chunk)
            digest.update(chunk)
            yield chunk
        state.sha256 = digest.hexdigest()
        after = _identity(os.fstat(descriptor))
        if after != local_ref.file_identity:
            raise SourceChangedError(expected_entry.relative_path.value)
        state.complete = True

    def download_to(self, item: DownloadItem, descriptor: int) -> None:
        """Stream one GET into an exclusive temporary descriptor and verify bytes."""
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with self._client.stream(
                "GET",
                item.request.url,
                headers=dict(item.request.headers),
            ) as response:
                if response.status_code != 200:
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        raise RetryableTransferError(
                            f"Download is temporarily unavailable for {item.entry.relative_path.value}."
                        )
                    raise TransferRejectedError(
                        f"Storage rejected download for {item.entry.relative_path.value} with "
                        f"status {response.status_code}."
                    )
                for chunk in response.iter_bytes(chunk_size=self._chunk_size):
                    size_bytes += len(chunk)
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(descriptor, view)
                        view = view[written:]
        except httpx.RequestError as error:
            raise RetryableTransferError(
                f"Download failed for {item.entry.relative_path.value}; retry pull."
            ) from error
        if size_bytes != item.entry.size_bytes or digest.hexdigest() != item.entry.sha256.value:
            raise StoredObjectMismatchError(
                f"Downloaded bytes do not match {item.entry.relative_path.value}."
            )
        os.fsync(descriptor)


@contextmanager
def _open_scanned_file(local_ref: LocalFileRef, expected_entry: ManifestEntry) -> Iterator[int]:
    descriptors: list[int] = []
    try:
        root_fd = os.open(local_ref.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(root_fd)
        if _identity(os.fstat(root_fd)) != local_ref.root_identity:
            raise SourceChangedError(expected_entry.relative_path.value)
        parent_fd = root_fd
        for part in local_ref.raw_parts[:-1]:
            parent_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            descriptors.append(parent_fd)
        file_fd = os.open(
            local_ref.raw_parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        descriptors.append(file_fd)
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise UnsafeFileTypeError("Scanned source is no longer a regular file.")
        if _identity(file_stat) != local_ref.file_identity:
            raise SourceChangedError(expected_entry.relative_path.value)
        yield file_fd
    except OSError as error:
        raise SourceChangedError(expected_entry.relative_path.value) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
