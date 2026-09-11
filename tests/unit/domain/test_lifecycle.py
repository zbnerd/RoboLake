"""Behavior tests for explicit RoboLake lifecycle transitions."""

import pytest
from robolake.domain.errors import IllegalTransitionError
from robolake.domain.lifecycle import (
    BlobState,
    UploadSessionState,
    VersionState,
    require_blob_transition,
    require_session_transition,
    require_version_transition,
)


def test_version_transition_accepts_approved_edges_and_idempotent_replay() -> None:
    assert (
        require_version_transition(VersionState.DRAFT, VersionState.UPLOADING)
        is VersionState.UPLOADING
    )
    assert require_version_transition(VersionState.READY, VersionState.READY) is VersionState.READY


def test_version_transition_rejects_ready_regression() -> None:
    with pytest.raises(IllegalTransitionError):
        require_version_transition(VersionState.READY, VersionState.UPLOADING)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (VersionState.DRAFT, VersionState.VERIFYING),
        (VersionState.UPLOADING, VersionState.VERIFYING),
        (VersionState.VERIFYING, VersionState.READY),
        (VersionState.VERIFYING, VersionState.FAILED),
        (VersionState.FAILED, VersionState.UPLOADING),
    ],
)
def test_version_transition_accepts_every_approved_publication_edge(
    current: VersionState, target: VersionState
) -> None:
    assert require_version_transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (BlobState.PENDING, BlobState.UPLOADING),
        (BlobState.UPLOADING, BlobState.VERIFYING),
        (BlobState.VERIFYING, BlobState.AVAILABLE),
        (BlobState.VERIFYING, BlobState.FAILED),
        (BlobState.FAILED, BlobState.UPLOADING),
    ],
)
def test_blob_transition_accepts_only_verification_lifecycle_edges(
    current: BlobState, target: BlobState
) -> None:
    assert require_blob_transition(current, target) is target
    with pytest.raises(IllegalTransitionError):
        require_blob_transition(BlobState.AVAILABLE, BlobState.PENDING)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (UploadSessionState.CREATED, UploadSessionState.IN_PROGRESS),
        (UploadSessionState.IN_PROGRESS, UploadSessionState.COMPLETED),
        (UploadSessionState.IN_PROGRESS, UploadSessionState.FAILED),
    ],
)
def test_upload_session_transition_accepts_m1_attempt_edges(
    current: UploadSessionState, target: UploadSessionState
) -> None:
    assert require_session_transition(current, target) is target

    with pytest.raises(IllegalTransitionError):
        require_session_transition(UploadSessionState.COMPLETED, UploadSessionState.IN_PROGRESS)


def test_every_unlisted_lifecycle_edge_is_rejected() -> None:
    version_edges = {
        (VersionState.DRAFT, VersionState.UPLOADING),
        (VersionState.DRAFT, VersionState.VERIFYING),
        (VersionState.UPLOADING, VersionState.VERIFYING),
        (VersionState.VERIFYING, VersionState.READY),
        (VersionState.VERIFYING, VersionState.FAILED),
        (VersionState.FAILED, VersionState.UPLOADING),
    }
    for current in VersionState:
        for target in VersionState:
            if current != target and (current, target) not in version_edges:
                with pytest.raises(IllegalTransitionError):
                    require_version_transition(current, target)

    blob_edges = {
        (BlobState.PENDING, BlobState.UPLOADING),
        (BlobState.UPLOADING, BlobState.VERIFYING),
        (BlobState.VERIFYING, BlobState.AVAILABLE),
        (BlobState.VERIFYING, BlobState.FAILED),
        (BlobState.FAILED, BlobState.UPLOADING),
    }
    for current in BlobState:
        for target in BlobState:
            if current != target and (current, target) not in blob_edges:
                with pytest.raises(IllegalTransitionError):
                    require_blob_transition(current, target)

    session_edges = {
        (UploadSessionState.CREATED, UploadSessionState.IN_PROGRESS),
        (UploadSessionState.IN_PROGRESS, UploadSessionState.COMPLETED),
        (UploadSessionState.IN_PROGRESS, UploadSessionState.FAILED),
    }
    for current in UploadSessionState:
        for target in UploadSessionState:
            if current != target and (current, target) not in session_edges:
                with pytest.raises(IllegalTransitionError):
                    require_session_transition(current, target)
