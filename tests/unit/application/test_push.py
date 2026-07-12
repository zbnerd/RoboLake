"""Push vertical-slice orchestration tests."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from robolake.application.contracts import (
    FileIdentity,
    LocalFileRef,
    PresignedRequest,
    PutOutcome,
    PutReceipt,
    ScannedDataset,
    ScannedFile,
    UploadPreparation,
)
from robolake.application.push import PushWorkflow
from robolake.domain.errors import UnsafeFileTypeError, UploadConflictError
from robolake.domain.identifiers import DatasetName, RelativePath, Sha256Digest
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import DatasetRecord, VersionRecord


def _scan() -> ScannedDataset:
    shared = Sha256Digest.parse("a" * 64)
    unique = Sha256Digest.parse("b" * 64)
    entries = (
        ManifestEntry(RelativePath.parse("a.bin"), 3, shared),
        ManifestEntry(RelativePath.parse("copy/a.bin"), 3, shared),
        ManifestEntry(RelativePath.parse("z.bin"), 2, unique),
    )
    identity = FileIdentity(1, 1, 3, 1)
    files = tuple(
        ScannedFile(
            entry,
            LocalFileRef(
                Path("/never-serialized"),
                identity,
                tuple(entry.relative_path.value.split("/")),
                identity,
            ),
        )
        for entry in entries
    )
    return ScannedDataset(Path("/never-serialized"), Manifest.build(entries), files)


@dataclass
class FakeScanner:
    result: ScannedDataset | Exception
    calls: int = 0

    def scan(self, source: Path) -> ScannedDataset:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class FakeControlPlane:
    scan: ScannedDataset
    ready_registration: bool = False
    fail_complete: bool = False
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.dataset = DatasetRecord(uuid4(), DatasetName.parse("demo/push"))
        self.version = VersionRecord(
            id=uuid4(),
            dataset_id=self.dataset.id,
            dataset_name=self.dataset.name,
            version_number=1,
            manifest_sha256=self.scan.manifest.sha256,
            state=VersionState.READY if self.ready_registration else VersionState.DRAFT,
            file_count=self.scan.manifest.file_count,
            logical_bytes=self.scan.manifest.logical_bytes,
            unique_blob_count=self.scan.manifest.unique_blob_count,
            unique_blob_bytes=self.scan.manifest.unique_blob_bytes,
        )
        self.sessions: dict[Sha256Digest, UUID] = {}

    def create_dataset(self, name: DatasetName, idempotency_key: str) -> DatasetRecord:
        self.calls.append(("dataset", name, idempotency_key))
        return replace(self.dataset, name=name)

    def register_version(
        self, dataset_id: UUID, manifest: Manifest, idempotency_key: str
    ) -> VersionRecord:
        self.calls.append(("version", dataset_id, manifest.sha256, idempotency_key))
        return replace(self.version, dataset_name=self.dataset.name)

    def prepare_upload(
        self, version_id: UUID, digest: Sha256Digest, idempotency_key: str
    ) -> UploadPreparation:
        self.calls.append(("prepare", digest, idempotency_key))
        available = digest.value.startswith("a")
        session_id = None if available else self.sessions.setdefault(digest, uuid4())
        return UploadPreparation(self.version, digest, 3 if available else 2, available, session_id)

    def upload_url(self, session_id: UUID) -> PresignedRequest:
        self.calls.append(("url", session_id))
        return PresignedRequest("http://storage.invalid/put?secret=yes", {})

    def complete_upload(self, session_id: UUID, etag: str | None) -> UploadPreparation:
        self.calls.append(("complete", session_id, etag))
        if self.fail_complete:
            raise UploadConflictError("retry")
        digest = next(
            digest for digest, identifier in self.sessions.items() if identifier == session_id
        )
        return UploadPreparation(self.version, digest, 2, True, session_id)

    def finalize(self, version_id: UUID) -> VersionRecord:
        self.calls.append(("finalize", version_id))
        return replace(self.version, state=VersionState.READY)


@dataclass
class FakeByteTransfer:
    outcome: PutOutcome
    calls: list[tuple[LocalFileRef, ManifestEntry]] = field(default_factory=list)

    def put(
        self,
        local_ref: LocalFileRef,
        request: PresignedRequest,
        expected_entry: ManifestEntry,
    ) -> PutReceipt:
        self.calls.append((local_ref, expected_entry))
        return PutReceipt(self.outcome, '"etag"')


def test_push_resolves_unique_blobs_and_separates_created_from_reused() -> None:
    scanned = _scan()
    control = FakeControlPlane(scanned)
    transfer = FakeByteTransfer(PutOutcome.CREATED)
    progress = []
    workflow = PushWorkflow(FakeScanner(scanned), control, transfer, progress.append)

    result = workflow.run(Path("/source"), DatasetName.parse("demo/push"))

    assert result.state is VersionState.READY
    assert result.file_count == 3
    assert result.logical_bytes == 8
    assert result.unique_blob_count == 2
    assert result.unique_blob_bytes == 5
    assert result.created_blob_count == 1
    assert result.created_blob_bytes == 2
    assert result.reused_blob_count == 1
    assert result.reused_blob_bytes == 3
    assert len([call for call in control.calls if call[0] == "prepare"]) == 2
    assert len(transfer.calls) == 1
    assert control.calls[-1] == ("finalize", control.version.id)


def test_scanner_failure_has_no_control_plane_or_transfer_side_effect() -> None:
    scanned = _scan()
    control = FakeControlPlane(scanned)
    transfer = FakeByteTransfer(PutOutcome.CREATED)
    workflow = PushWorkflow(
        FakeScanner(UnsafeFileTypeError("unsafe")), control, transfer, lambda _: None
    )

    with pytest.raises(UnsafeFileTypeError):
        workflow.run(Path("/source"), DatasetName.parse("demo/push"))

    assert control.calls == []
    assert transfer.calls == []


@pytest.mark.parametrize("outcome", [PutOutcome.ALREADY_EXISTS, PutOutcome.CONFLICT])
def test_reconciled_conditional_conflicts_count_as_reused(outcome: PutOutcome) -> None:
    scanned = _scan()
    control = FakeControlPlane(scanned)
    result = PushWorkflow(
        FakeScanner(scanned), control, FakeByteTransfer(outcome), lambda _: None
    ).run(Path("/source"), DatasetName.parse("demo/push"))

    assert result.created_blob_count == 0
    assert result.reused_blob_count == 2


def test_unresolved_conflict_does_not_finalize() -> None:
    scanned = _scan()
    control = FakeControlPlane(scanned, fail_complete=True)
    workflow = PushWorkflow(
        FakeScanner(scanned), control, FakeByteTransfer(PutOutcome.CONFLICT), lambda _: None
    )

    with pytest.raises(UploadConflictError):
        workflow.run(Path("/source"), DatasetName.parse("demo/push"))

    assert not any(call[0] == "finalize" for call in control.calls)
