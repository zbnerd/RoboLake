"""Stable CLI progress, error, and exit-code rendering."""

from __future__ import annotations

import typer
from robolake.application.contracts import ProgressEvent, ProgressKind
from robolake.domain.errors import (
    ApiProtocolError,
    ApiUnavailableError,
    ContentConflictError,
    IdempotencyConflictError,
    IllegalTransitionError,
    InvalidCursorError,
    InvalidDatasetName,
    InvalidDatasetReference,
    InvalidDigestError,
    ManifestMismatchError,
    NotFoundError,
    PathCollisionError,
    RetryableTransferError,
    RoboLakeError,
    SourceChangedError,
    StoredObjectMismatchError,
    TransferRejectedError,
    UnsafeFileTypeError,
    UnsafePathError,
    UnsupportedFileSizeError,
    UnsupportedPlatformError,
    UploadConflictError,
)

_VALIDATION = (
    InvalidCursorError,
    InvalidDatasetName,
    InvalidDatasetReference,
    InvalidDigestError,
    PathCollisionError,
    SourceChangedError,
    UnsafeFileTypeError,
    UnsafePathError,
    UnsupportedFileSizeError,
    UnsupportedPlatformError,
)
_CONFLICT = (ContentConflictError, IdempotencyConflictError, IllegalTransitionError, NotFoundError)
_RETRYABLE = (ApiUnavailableError, RetryableTransferError, UploadConflictError)
_INTEGRITY = (ManifestMismatchError, StoredObjectMismatchError, TransferRejectedError)


def render_progress(event: ProgressEvent) -> None:
    """Render only logical safe paths and unique-Blob progress."""
    if event.kind is ProgressKind.SCANNED:
        return
    path = f" {event.relative_path.value}" if event.relative_path is not None else ""
    typer.echo(
        f"{event.kind.value}{path}: {event.resolved_blob_count}/{event.unique_blob_count} blobs "
        f"({event.resolved_blob_bytes}/{event.unique_blob_bytes} unique bytes)",
        err=True,
    )


def exit_for_error(error: RoboLakeError) -> int:
    if isinstance(error, _VALIDATION):
        return 2
    if isinstance(error, _CONFLICT):
        return 3
    if isinstance(error, _RETRYABLE):
        return 4
    if isinstance(error, _INTEGRITY):
        return 5
    if isinstance(error, ApiProtocolError):
        return 1
    return 1


def render_error(error: RoboLakeError) -> None:
    """Print symbolic cause/action without provider details or local absolute roots."""
    actions = {
        "STORED_OBJECT_MISMATCH": "Contact the storage operator; automatic repair is disabled.",
        "UPLOAD_CONFLICT": "Rerun push to reconcile the deterministic object key.",
        "API_UNAVAILABLE": "Retry after the RoboLake API is reachable.",
        "NOT_FOUND": "Check the Dataset reference and retry.",
    }
    typer.echo(
        f"Error [{error.code}]: {actions.get(error.code, 'Operation stopped safely.')}", err=True
    )
