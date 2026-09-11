"""Explicit RoboLake publication and upload lifecycle rules."""

from __future__ import annotations

from enum import StrEnum

from robolake.domain.errors import IllegalTransitionError


class VersionState(StrEnum):
    """DatasetVersion publication states."""

    DRAFT = "DRAFT"
    UPLOADING = "UPLOADING"
    VERIFYING = "VERIFYING"
    READY = "READY"
    FAILED = "FAILED"


class BlobState(StrEnum):
    """Physical Blob verification states."""

    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    VERIFYING = "VERIFYING"
    AVAILABLE = "AVAILABLE"
    FAILED = "FAILED"


class UploadSessionState(StrEnum):
    """M1 single-PUT attempt states."""

    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class NextAction(StrEnum):
    """Derived safe caller guidance."""

    NONE = "NONE"
    RETRY_PUSH = "RETRY_PUSH"
    RETRY_STATUS = "RETRY_STATUS"
    CONTACT_OPERATOR = "CONTACT_OPERATOR"


class FailureCode(StrEnum):
    """Persisted M1 terminal publication causes."""

    STORED_OBJECT_MISMATCH = "STORED_OBJECT_MISMATCH"


_VERSION_EDGES = {
    (VersionState.DRAFT, VersionState.UPLOADING),
    (VersionState.DRAFT, VersionState.VERIFYING),
    (VersionState.UPLOADING, VersionState.VERIFYING),
    (VersionState.VERIFYING, VersionState.READY),
    (VersionState.VERIFYING, VersionState.FAILED),
    (VersionState.FAILED, VersionState.UPLOADING),
}

_BLOB_EDGES = {
    (BlobState.PENDING, BlobState.UPLOADING),
    (BlobState.UPLOADING, BlobState.VERIFYING),
    (BlobState.VERIFYING, BlobState.AVAILABLE),
    (BlobState.VERIFYING, BlobState.FAILED),
    (BlobState.FAILED, BlobState.UPLOADING),
}

_SESSION_EDGES = {
    (UploadSessionState.CREATED, UploadSessionState.IN_PROGRESS),
    (UploadSessionState.IN_PROGRESS, UploadSessionState.COMPLETED),
    (UploadSessionState.IN_PROGRESS, UploadSessionState.FAILED),
}


def require_version_transition(current: VersionState, target: VersionState) -> VersionState:
    """Return target when the Version transition is legal or idempotent."""
    if current == target or (current, target) in _VERSION_EDGES:
        return target
    raise IllegalTransitionError(f"Version cannot transition from {current} to {target}.")


def require_blob_transition(current: BlobState, target: BlobState) -> BlobState:
    """Return target when the Blob transition is legal or idempotent."""
    if current == target or (current, target) in _BLOB_EDGES:
        return target
    raise IllegalTransitionError(f"Blob cannot transition from {current} to {target}.")


def require_session_transition(
    current: UploadSessionState, target: UploadSessionState
) -> UploadSessionState:
    """Return target when the M1 UploadSession transition is legal or idempotent."""
    if current == target or (current, target) in _SESSION_EDGES:
        return target
    raise IllegalTransitionError(f"UploadSession cannot transition from {current} to {target}.")
