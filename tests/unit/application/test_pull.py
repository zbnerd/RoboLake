"""One-capability-at-a-time atomic pull workflow tests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from robolake.application.contracts import (
    DownloadCapability,
    DownloadItem,
    PreparedDownload,
    PresignedRequest,
)
from robolake.application.pull import PullWorkflow
from robolake.domain.errors import IllegalTransitionError
from robolake.domain.identifiers import DatasetName, DatasetReference, RelativePath, Sha256Digest
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import VersionRecord


def _manifest() -> Manifest:
    return Manifest.build(
        [
            ManifestEntry(RelativePath.parse("a.bin"), 1, Sha256Digest.parse("a" * 64)),
            ManifestEntry(RelativePath.parse("nested/b.bin"), 2, Sha256Digest.parse("b" * 64)),
        ]
    )


def _version(manifest: Manifest, state: VersionState = VersionState.READY) -> VersionRecord:
    return VersionRecord(
        id=uuid4(),
        dataset_id=uuid4(),
        dataset_name=DatasetName.parse("demo/pull"),
        version_number=1,
        manifest_sha256=manifest.sha256,
        state=state,
        file_count=manifest.file_count,
        logical_bytes=manifest.logical_bytes,
        unique_blob_count=manifest.unique_blob_count,
        unique_blob_bytes=manifest.unique_blob_bytes,
    )


@dataclass
class FakePullControl:
    version: VersionRecord
    stored_manifest: Manifest
    events: list[tuple[object, ...]]

    def resolve(self, reference: DatasetReference) -> VersionRecord:
        self.events.append(("resolve", reference))
        return self.version

    def manifest(self, version_id: UUID) -> Manifest:
        self.events.append(("manifest", version_id))
        return self.stored_manifest

    def download_plan(self, version_id: UUID, cursor: str | None) -> DownloadCapability:
        self.events.append(("capability", cursor))
        ordinal = 0 if cursor is None else int(cursor)
        entry = self.stored_manifest.entries[ordinal]
        next_cursor = str(ordinal + 1) if ordinal + 1 < self.stored_manifest.file_count else None
        return DownloadCapability(
            DownloadItem(entry, PresignedRequest(f"http://storage/{ordinal}?secret=yes", {})),
            next_cursor,
            next_cursor is None,
        )


@dataclass
class FakeTree:
    events: list[tuple[object, ...]]
    fail_write_ordinal: int | None = None
    prepared: int = 0

    def begin(self, output: Path, manifest: Manifest) -> None:
        self.events.append(("begin", output, manifest.sha256))

    def prepare(self, entry: ManifestEntry) -> PreparedDownload:
        ordinal = self.prepared
        self.prepared += 1
        self.events.append(("prepare", ordinal, entry.relative_path))
        return PreparedDownload(ordinal)

    def write(self, prepared: PreparedDownload, item: DownloadItem) -> None:
        self.events.append(("write", prepared.token, item.entry.relative_path))
        if prepared.token == self.fail_write_ordinal:
            raise RuntimeError("synthetic write failure")

    def commit(self) -> Path:
        self.events.append(("commit",))
        return Path("output")

    def abort(self) -> None:
        self.events.append(("abort",))


def test_pull_prepares_before_each_capability_and_advances_only_after_write() -> None:
    manifest = _manifest()
    version = _version(manifest)
    events: list[tuple[object, ...]] = []
    workflow = PullWorkflow(FakePullControl(version, manifest, events), FakeTree(events))

    result = workflow.run(version.reference, Path("output"))

    assert result.reference == version.reference
    assert result.output == Path("output")
    assert result.materialized_file_count == 2
    assert result.materialized_bytes == 3
    assert events == [
        ("resolve", version.reference),
        ("manifest", version.id),
        ("begin", Path("output"), manifest.sha256),
        ("prepare", 0, manifest.entries[0].relative_path),
        ("capability", None),
        ("write", 0, manifest.entries[0].relative_path),
        ("prepare", 1, manifest.entries[1].relative_path),
        ("capability", "1"),
        ("write", 1, manifest.entries[1].relative_path),
        ("commit",),
    ]


def test_pull_refuses_nonready_before_staging_and_aborts_write_failure() -> None:
    manifest = _manifest()
    draft = _version(manifest, VersionState.DRAFT)
    events: list[tuple[object, ...]] = []
    workflow = PullWorkflow(FakePullControl(draft, manifest, events), FakeTree(events))

    with pytest.raises(IllegalTransitionError):
        workflow.run(draft.reference, Path("output"))
    assert events == [("resolve", draft.reference)]

    ready = replace(draft, state=VersionState.READY)
    events = []
    workflow = PullWorkflow(
        FakePullControl(ready, manifest, events),
        FakeTree(events, fail_write_ordinal=0),
    )
    with pytest.raises(RuntimeError):
        workflow.run(ready.reference, Path("output"))
    assert events[-1] == ("abort",)
    assert not any(event[0] == "commit" for event in events)


def test_empty_ready_pull_commits_without_requesting_capability() -> None:
    manifest = Manifest.build([])
    version = _version(manifest)
    events: list[tuple[object, ...]] = []
    result = PullWorkflow(FakePullControl(version, manifest, events), FakeTree(events)).run(
        version.reference, Path("empty")
    )

    assert result.materialized_file_count == 0
    assert events == [
        ("resolve", version.reference),
        ("manifest", version.id),
        ("begin", Path("empty"), manifest.sha256),
        ("commit",),
    ]
