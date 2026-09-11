"""Behavior tests for framework-free RoboLake records and status views."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from robolake.domain.errors import ContentConflictError
from robolake.domain.identifiers import DatasetName, Sha256Digest
from robolake.domain.lifecycle import (
    BlobState,
    FailureCode,
    NextAction,
    UploadSessionState,
    VersionState,
)
from robolake.domain.records import (
    BlobRecord,
    ContentStatus,
    DatasetRecord,
    SnapshotStatus,
    UploadSessionRecord,
    VersionRecord,
    VersionStatus,
)


def test_version_record_derives_reference_and_empty_without_persisted_flag() -> None:
    version = VersionRecord(
        id=uuid4(),
        dataset_id=uuid4(),
        dataset_name=DatasetName.parse("demo/empty"),
        version_number=1,
        manifest_sha256=Sha256Digest.parse("0" * 64),
        state=VersionState.DRAFT,
        file_count=0,
        logical_bytes=0,
        unique_blob_count=0,
        unique_blob_bytes=0,
    )

    assert str(version.reference) == "demo/empty@v1"
    assert version.is_empty is True


def test_ready_empty_status_is_complete_without_zero_division() -> None:
    version = VersionRecord(
        id=uuid4(),
        dataset_id=uuid4(),
        dataset_name=DatasetName.parse("demo/empty"),
        version_number=1,
        manifest_sha256=Sha256Digest.parse("0" * 64),
        state=VersionState.READY,
        file_count=0,
        logical_bytes=0,
        unique_blob_count=0,
        unique_blob_bytes=0,
    )
    status = VersionStatus(
        version=version,
        snapshot=SnapshotStatus(0, 0, 0, 0),
        content=ContentStatus(0, 0, 0, 0),
    )

    assert status.snapshot_completion_percent == 100.0
    assert status.content_completion_percent == 100.0
    assert status.next_action is NextAction.NONE


def test_poisoned_referenced_blob_derives_operator_action() -> None:
    version = VersionRecord(
        id=uuid4(),
        dataset_id=uuid4(),
        dataset_name=DatasetName.parse("demo/blocked"),
        version_number=1,
        manifest_sha256=Sha256Digest.parse("1" * 64),
        state=VersionState.UPLOADING,
        file_count=1,
        logical_bytes=10,
        unique_blob_count=1,
        unique_blob_bytes=10,
    )
    status = VersionStatus(
        version=version,
        snapshot=SnapshotStatus(1, 10, 0, 0),
        content=ContentStatus(1, 10, 0, 0),
        blocking_failure_code=FailureCode.STORED_OBJECT_MISMATCH,
    )

    assert status.next_action is NextAction.CONTACT_OPERATOR


def test_status_views_reject_completed_values_above_totals() -> None:
    with pytest.raises(ContentConflictError):
        SnapshotStatus(1, 10, 2, 10)
    with pytest.raises(ContentConflictError):
        ContentStatus(1, 10, 2, 10)


def test_status_views_reject_invalid_byte_and_negative_totals() -> None:
    with pytest.raises(ContentConflictError):
        SnapshotStatus(1, 10, 1, 11)
    with pytest.raises(ContentConflictError):
        ContentStatus(1, 10, 1, 11)
    with pytest.raises(ContentConflictError):
        SnapshotStatus(-1, 0, 0, 0)


def test_registry_records_are_immutable_value_snapshots() -> None:
    dataset = DatasetRecord(id=uuid4(), name=DatasetName.parse("demo/records"))
    blob = BlobRecord(
        id=uuid4(),
        sha256=Sha256Digest.parse("2" * 64),
        size_bytes=2,
        object_key="blobs/sha256/22/22/" + "2" * 64,
        state=BlobState.PENDING,
    )
    session = UploadSessionRecord(
        id=uuid4(),
        blob_id=blob.id,
        initiating_version_id=uuid4(),
        state=UploadSessionState.CREATED,
    )

    assert dataset.name.value == "demo/records"
    assert session.blob_id == blob.id
    with pytest.raises(FrozenInstanceError):
        blob.state = BlobState.AVAILABLE  # type: ignore[misc]


@pytest.mark.parametrize(
    ("state", "expected_action"),
    [
        (VersionState.DRAFT, NextAction.RETRY_PUSH),
        (VersionState.VERIFYING, NextAction.RETRY_STATUS),
    ],
)
def test_empty_nonready_status_omits_percent_and_derives_action(
    state: VersionState, expected_action: NextAction
) -> None:
    version = VersionRecord(
        id=uuid4(),
        dataset_id=uuid4(),
        dataset_name=DatasetName.parse("demo/pending-empty"),
        version_number=1,
        manifest_sha256=Sha256Digest.parse("3" * 64),
        state=state,
        file_count=0,
        logical_bytes=0,
        unique_blob_count=0,
        unique_blob_bytes=0,
    )
    status = VersionStatus(version, SnapshotStatus(0, 0, 0, 0), ContentStatus(0, 0, 0, 0))

    assert status.snapshot_completion_percent is None
    assert status.content_completion_percent is None
    assert status.next_action is expected_action
