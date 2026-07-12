"""Public M1 Dataset command output tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from apps.cli.main import CliServices, app
from robolake.application.contracts import PullResult, PushResult
from robolake.domain.identifiers import DatasetName, DatasetReference, RelativePath, Sha256Digest
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import ContentStatus, SnapshotStatus, VersionRecord, VersionStatus
from typer.testing import CliRunner

pytestmark = pytest.mark.unit
runner = CliRunner()


def _version(*, empty: bool = False) -> VersionRecord:
    return VersionRecord(
        id=uuid4(),
        dataset_id=uuid4(),
        dataset_name=DatasetName.parse("demo/pick-place"),
        version_number=1,
        manifest_sha256=Sha256Digest.parse("a" * 64),
        state=VersionState.READY,
        file_count=0 if empty else 2,
        logical_bytes=0 if empty else 12,
        unique_blob_count=0 if empty else 1,
        unique_blob_bytes=0 if empty else 6,
    )


@dataclass
class FakePush:
    result: PushResult

    def run(self, source: Path, dataset: DatasetName) -> PushResult:
        assert source == Path("source")
        assert dataset == DatasetName.parse("demo/pick-place")
        return self.result


@dataclass
class FakePull:
    def run(self, reference: DatasetReference, output: Path) -> PullResult:
        assert reference == DatasetReference.parse("demo/pick-place@v1")
        return PullResult(reference, output, 2, 12)


@dataclass
class FakeControl:
    version: VersionRecord
    stored_manifest: Manifest

    def resolve(self, reference: DatasetReference) -> VersionRecord:
        assert reference == self.version.reference
        return self.version

    def status(self, version_id: object) -> VersionStatus:
        assert version_id == self.version.id
        return VersionStatus(
            self.version,
            SnapshotStatus(
                self.version.file_count,
                self.version.logical_bytes,
                self.version.file_count,
                self.version.logical_bytes,
            ),
            ContentStatus(
                self.version.unique_blob_count,
                self.version.unique_blob_bytes,
                self.version.unique_blob_count,
                self.version.unique_blob_bytes,
            ),
        )

    def manifest(self, version_id: object) -> Manifest:
        assert version_id == self.version.id
        return self.stored_manifest


def _services(*, empty: bool = False) -> CliServices:
    version = _version(empty=empty)
    manifest = (
        Manifest.build([])
        if empty
        else Manifest.build(
            [
                ManifestEntry(
                    RelativePath.parse("file.bin"),
                    6,
                    Sha256Digest.parse("b" * 64),
                )
            ]
        )
    )
    result = PushResult(
        reference=version.reference,
        state=VersionState.READY,
        file_count=version.file_count,
        logical_bytes=version.logical_bytes,
        unique_blob_count=version.unique_blob_count,
        unique_blob_bytes=version.unique_blob_bytes,
        created_blob_count=version.unique_blob_count,
        created_blob_bytes=version.unique_blob_bytes,
        reused_blob_count=0,
        reused_blob_bytes=0,
    )
    return CliServices(
        control_plane=cast(Any, FakeControl(version, manifest)),
        push_workflow=cast(Any, FakePush(result)),
        pull_workflow=cast(Any, FakePull()),
        close=lambda: None,
    )


def test_push_prints_scanned_summary_to_stderr_and_ready_reference_to_stdout() -> None:
    result = runner.invoke(
        app,
        ["push", "source", "--dataset", "demo/pick-place"],
        obj=_services(),
    )

    assert result.exit_code == 0
    assert result.stdout == "READY demo/pick-place@v1\n"
    assert "Scanned 2 files (12 bytes)" in result.stderr


@pytest.mark.parametrize(
    ("empty", "expected"),
    [
        (
            False,
            "State: READY, Snapshot: 2/2 files (12/12 logical bytes), "
            "Content: 1/1 blobs (6/6 unique bytes), Empty: no",
        ),
        (
            True,
            "State: READY, Snapshot: 0/0 files (0/0 logical bytes), "
            "Content: 0/0 blobs (0/0 unique bytes), Empty: yes",
        ),
    ],
)
def test_status_renders_logical_and_unique_content_views(empty: bool, expected: str) -> None:
    result = runner.invoke(app, ["status", "demo/pick-place@v1"], obj=_services(empty=empty))

    assert result.exit_code == 0
    assert expected in result.stdout


def test_manifest_writes_exact_canonical_bytes_without_newline() -> None:
    services = _services()
    result = runner.invoke(app, ["manifest", "demo/pick-place@v1"], obj=services)

    assert result.exit_code == 0
    expected = cast(FakeControl, services.control_plane).stored_manifest.canonical_bytes
    assert result.stdout_bytes == expected


def test_pull_reports_local_atomic_publication_path() -> None:
    result = runner.invoke(
        app,
        ["pull", "demo/pick-place@v1", "--output", "restored"],
        obj=_services(),
    )

    assert result.exit_code == 0
    assert result.stdout == "RESTORED demo/pick-place@v1 -> restored\n"
