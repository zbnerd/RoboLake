"""Transfer orchestration and cursor contract tests."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import pytest
from robolake.application.contracts import ObjectInfo, PresignedRequest, UploadContext
from robolake.application.transfers import TransferService, decode_cursor, encode_cursor
from robolake.domain.errors import (
    IllegalTransitionError,
    InvalidCursorError,
    StoredObjectMismatchError,
    UploadConflictError,
)
from robolake.domain.identifiers import DatasetName, RelativePath, Sha256Digest, object_key_for
from robolake.domain.lifecycle import BlobState, FailureCode, UploadSessionState, VersionState
from robolake.domain.manifest import ManifestEntry
from robolake.domain.records import (
    BlobRecord,
    ContentStatus,
    SnapshotStatus,
    UploadSessionRecord,
    VersionRecord,
    VersionStatus,
)


def test_cursor_round_trips_one_version_and_ordinal_canonically() -> None:
    version_id = uuid4()
    cursor = encode_cursor(version_id, 42)

    assert "=" not in cursor
    assert decode_cursor(cursor, version_id, file_count=100) == 42
    assert encode_cursor(version_id, decode_cursor(cursor, version_id, 100)) == cursor


def test_cursor_rejects_noncanonical_wrong_version_and_out_of_range_values() -> None:
    version_id = uuid4()
    cursor = encode_cursor(version_id, 1)

    with pytest.raises(InvalidCursorError):
        decode_cursor(f"{cursor}=", version_id, file_count=2)
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, uuid4(), file_count=2)
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, version_id, file_count=1)
    with pytest.raises(InvalidCursorError):
        decode_cursor("not-base64!", version_id, file_count=2)


def _context(blob_state: BlobState = BlobState.AVAILABLE) -> UploadContext:
    version_id = uuid4()
    digest = Sha256Digest.parse("a" * 64)
    blob = BlobRecord(
        id=uuid4(),
        sha256=digest,
        size_bytes=3,
        object_key=object_key_for(digest),
        state=blob_state,
    )
    version = VersionRecord(
        id=version_id,
        dataset_id=uuid4(),
        dataset_name=DatasetName.parse("demo/transfer"),
        version_number=1,
        manifest_sha256=Sha256Digest.parse("b" * 64),
        state=VersionState.UPLOADING,
        file_count=1,
        logical_bytes=3,
        unique_blob_count=1,
        unique_blob_bytes=3,
    )
    session = UploadSessionRecord(
        id=uuid4(),
        blob_id=blob.id,
        initiating_version_id=version_id,
        state=UploadSessionState.CREATED,
    )
    return UploadContext(version, blob, session)


@dataclass
class AvailableStore:
    context: UploadContext
    events: list[str] = field(default_factory=list)

    def prepare_upload(
        self,
        version_id: UUID,
        sha256: Sha256Digest,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> UploadContext:
        assert version_id == self.context.version.id
        assert sha256 == self.context.blob.sha256
        assert idempotency_key == "upload-key"
        assert request_sha256.value != sha256.value
        return self.context

    def get_upload(self, session_id: UUID) -> UploadContext:
        assert self.context.session is not None
        assert session_id == self.context.session.id
        return self.context

    def mark_upload_in_progress(self, session_id: UUID) -> UploadContext:
        assert self.context.session is not None
        assert session_id == self.context.session.id
        self.events.append("in-progress")
        self.context = UploadContext(
            self.context.version,
            replace(self.context.blob, state=BlobState.UPLOADING),
            replace(self.context.session, state=UploadSessionState.IN_PROGRESS),
        )
        return self.context

    def mark_upload_verified(self, session_id: UUID, etag: str | None) -> UploadContext:
        assert self.context.session is not None
        assert session_id == self.context.session.id
        self.events.append("verified")
        self.context = UploadContext(
            self.context.version,
            replace(self.context.blob, state=BlobState.AVAILABLE),
            replace(self.context.session, state=UploadSessionState.COMPLETED, etag=etag),
        )
        return self.context

    def mark_upload_failed(self, session_id: UUID, code: FailureCode, detail: str) -> UploadContext:
        assert self.context.session is not None
        assert session_id == self.context.session.id
        assert code is FailureCode.STORED_OBJECT_MISMATCH
        assert detail == "Stored object does not match its content address."
        self.events.append("failed")
        self.context = UploadContext(
            replace(
                self.context.version,
                state=VersionState.FAILED,
                failure_code=code,
                failure_detail=detail,
            ),
            replace(self.context.blob, state=BlobState.FAILED, failure_code=code),
            replace(self.context.session, state=UploadSessionState.FAILED),
        )
        return self.context

    def restart_failed_upload(
        self,
        version_id: UUID,
        blob_id: UUID,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> UploadContext:
        assert version_id == self.context.version.id
        assert blob_id == self.context.blob.id
        assert idempotency_key == "upload-key"
        assert self.context.session is not None
        self.events.append("restarted")
        self.context = UploadContext(
            replace(
                self.context.version,
                state=VersionState.UPLOADING,
                failure_code=None,
                failure_detail=None,
            ),
            replace(self.context.blob, state=BlobState.UPLOADING, failure_code=None),
            UploadSessionRecord(
                id=uuid4(),
                blob_id=blob_id,
                initiating_version_id=version_id,
                state=UploadSessionState.CREATED,
            ),
        )
        return self.context


@dataclass
class UnusedObjectStore:
    head_calls: int = 0

    def head(self, object_key: str) -> None:
        self.head_calls += 1
        raise AssertionError(f"AVAILABLE content must not be checked again: {object_key}")


@dataclass
class MissingObjectStore:
    head_calls: int = 0

    def head(self, object_key: str) -> None:
        self.head_calls += 1
        return None


@dataclass
class ExistingObjectStore:
    info: ObjectInfo
    chunks: tuple[bytes, ...] = ()
    head_calls: int = 0
    iter_calls: int = 0

    def head(self, object_key: str) -> ObjectInfo:
        self.head_calls += 1
        return self.info

    def iter_bytes(self, object_key: str, chunk_size: int) -> Iterator[bytes]:
        self.iter_calls += 1
        yield from self.chunks


@dataclass
class SigningObjectStore:
    put_call: tuple[str, int, str, int] | None = None

    def presign_put(
        self,
        object_key: str,
        size_bytes: int,
        checksum_base64: str,
        expires_seconds: int,
    ) -> PresignedRequest:
        self.put_call = (object_key, size_bytes, checksum_base64, expires_seconds)
        return PresignedRequest(
            "http://storage.invalid/blob?X-Amz-Signature=secret",
            {
                "If-None-Match": "*",
                "Content-Length": str(size_bytes),
                "x-amz-checksum-sha256": checksum_base64,
            },
        )


@dataclass
class DownloadObjectStore:
    calls: list[str] = field(default_factory=list)

    def presign_get(self, object_key: str, expires_seconds: int) -> PresignedRequest:
        self.calls.append(object_key)
        return PresignedRequest(
            f"http://storage.invalid/{object_key}?generation={len(self.calls)}",
            {},
        )


@dataclass
class DownloadStore:
    status: VersionStatus
    entries: tuple[ManifestEntry, ...]
    finalize_calls: int = 0

    def get_status(self, version_id: UUID) -> VersionStatus:
        assert version_id == self.status.version.id
        return self.status

    def get_download_entry(self, version_id: UUID, manifest_ordinal: int) -> ManifestEntry | None:
        assert version_id == self.status.version.id
        return self.entries[manifest_ordinal] if manifest_ordinal < len(self.entries) else None

    def finalize_version(self, version_id: UUID) -> VersionRecord:
        assert version_id == self.status.version.id
        self.finalize_calls += 1
        return replace(self.status.version, state=VersionState.READY)


def test_available_blob_is_reused_without_storage_access() -> None:
    context = _context()
    objects = UnusedObjectStore()
    service = TransferService(AvailableStore(context), objects)

    prepared = service.prepare_upload(context.version.id, context.blob.sha256, "upload-key")

    assert prepared.available is True
    assert prepared.session_id is None
    assert prepared.blob_sha256 == context.blob.sha256
    assert objects.head_calls == 0


def test_missing_pending_blob_returns_persisted_session_for_direct_put() -> None:
    context = _context(BlobState.PENDING)
    objects = MissingObjectStore()
    service = TransferService(AvailableStore(context), objects)

    prepared = service.prepare_upload(context.version.id, context.blob.sha256, "upload-key")

    assert prepared.available is False
    assert context.session is not None
    assert prepared.session_id == context.session.id
    assert objects.head_calls == 1


def test_matching_provider_checksum_reconciles_without_streaming_object() -> None:
    context = _context(BlobState.PENDING)
    store = AvailableStore(context)
    objects = ExistingObjectStore(ObjectInfo(3, context.blob.sha256, '"etag"'))
    service = TransferService(store, objects)

    prepared = service.prepare_upload(context.version.id, context.blob.sha256, "upload-key")

    assert prepared.available is True
    assert context.session is not None
    assert prepared.session_id == context.session.id
    assert objects.head_calls == 1
    assert objects.iter_calls == 0
    assert store.events == ["in-progress", "verified"]


def test_missing_provider_checksum_uses_full_streamed_sha256_fallback() -> None:
    context = _context(BlobState.PENDING)
    digest = Sha256Digest.parse(hashlib.sha256(b"abc").hexdigest())
    context = UploadContext(
        context.version,
        replace(context.blob, sha256=digest, object_key=object_key_for(digest)),
        context.session,
    )
    store = AvailableStore(context)
    objects = ExistingObjectStore(ObjectInfo(3, None, None), (b"a", b"bc"))
    service = TransferService(store, objects, stream_chunk_bytes=2)

    prepared = service.prepare_upload(context.version.id, digest, "upload-key")

    assert prepared.available is True
    assert objects.iter_calls == 1
    assert store.events == ["in-progress", "verified"]


def test_mismatching_existing_object_is_failed_without_overwrite_or_delete() -> None:
    context = _context(BlobState.PENDING)
    store = AvailableStore(context)
    wrong = Sha256Digest.parse("f" * 64)
    objects = ExistingObjectStore(ObjectInfo(3, wrong, None))
    service = TransferService(store, objects)

    with pytest.raises(StoredObjectMismatchError):
        service.prepare_upload(context.version.id, context.blob.sha256, "upload-key")

    assert store.events == ["in-progress", "failed"]
    assert objects.iter_calls == 0


def test_issue_upload_url_marks_session_started_and_signs_exact_blob_contract() -> None:
    context = _context(BlobState.PENDING)
    assert context.session is not None
    store = AvailableStore(context)
    objects = SigningObjectStore()
    service = TransferService(store, objects, presigned_url_ttl_seconds=123)

    request = service.issue_upload_url(context.session.id)

    assert store.events == ["in-progress"]
    assert objects.put_call == (
        context.blob.object_key,
        context.blob.size_bytes,
        context.blob.sha256.checksum_base64,
        123,
    )
    assert request.headers["If-None-Match"] == "*"
    assert "secret" not in repr(request)


def test_complete_reconciles_matching_object_and_is_retryable_while_missing() -> None:
    created = _context(BlobState.PENDING)
    assert created.session is not None
    in_progress = UploadContext(
        created.version,
        replace(created.blob, state=BlobState.UPLOADING),
        replace(created.session, state=UploadSessionState.IN_PROGRESS),
    )
    store = AvailableStore(in_progress)
    objects = ExistingObjectStore(ObjectInfo(3, in_progress.blob.sha256, '"etag"'))
    service = TransferService(store, objects)

    completed = service.complete_upload(in_progress.session.id, '"client-etag"')

    assert completed.available is True
    assert store.events == ["verified"]

    missing_store = AvailableStore(in_progress)
    missing_service = TransferService(missing_store, MissingObjectStore())
    with pytest.raises(UploadConflictError):
        missing_service.complete_upload(in_progress.session.id)
    assert missing_store.events == []


def test_failed_blob_restarts_only_after_operator_removed_stored_key() -> None:
    context = _context(BlobState.FAILED)
    assert context.session is not None
    context = UploadContext(
        replace(context.version, state=VersionState.FAILED),
        context.blob,
        replace(context.session, state=UploadSessionState.FAILED),
    )
    store = AvailableStore(context)
    service = TransferService(store, MissingObjectStore())

    prepared = service.prepare_upload(context.version.id, context.blob.sha256, "upload-key")

    assert prepared.available is False
    assert prepared.session_id != context.session.id
    assert store.events == ["restarted"]


def _download_store(state: VersionState, entries: tuple[ManifestEntry, ...]) -> DownloadStore:
    base = _context().version
    logical_bytes = sum(entry.size_bytes for entry in entries)
    version = replace(
        base,
        state=state,
        file_count=len(entries),
        logical_bytes=logical_bytes,
        unique_blob_count=len({entry.sha256 for entry in entries}),
        unique_blob_bytes=sum({entry.sha256: entry.size_bytes for entry in entries}.values()),
    )
    return DownloadStore(
        VersionStatus(
            version,
            SnapshotStatus(len(entries), logical_bytes, len(entries), logical_bytes),
            ContentStatus(
                version.unique_blob_count,
                version.unique_blob_bytes,
                version.unique_blob_count,
                version.unique_blob_bytes,
            ),
        ),
        entries,
    )


def test_download_plan_is_ready_only_replayable_and_one_capability_at_a_time() -> None:
    digest = Sha256Digest.parse("6" * 64)
    entries = (
        ManifestEntry(RelativePath.parse("a.bin"), 1, digest),
        ManifestEntry(RelativePath.parse("b.bin"), 1, digest),
    )
    objects = DownloadObjectStore()
    draft = _download_store(VersionState.DRAFT, entries)
    with pytest.raises(IllegalTransitionError):
        TransferService(draft, objects).download_plan(draft.status.version.id, None)

    ready = _download_store(VersionState.READY, entries)
    service = TransferService(ready, objects, presigned_url_ttl_seconds=99)
    first = service.download_plan(ready.status.version.id, None)
    assert first.item is not None
    assert first.item.entry == entries[0]
    assert first.complete is False
    assert first.next_cursor is not None

    replay = service.download_plan(ready.status.version.id, first.next_cursor)
    replay_again = service.download_plan(ready.status.version.id, first.next_cursor)
    assert replay.item is not None
    assert replay_again.item is not None
    assert replay.item.entry == replay_again.item.entry == entries[1]
    assert replay.item.request.url != replay_again.item.request.url
    assert replay.complete is True
    assert replay.next_cursor is None


def test_empty_ready_download_needs_no_capability_and_finalize_delegates() -> None:
    ready = _download_store(VersionState.READY, ())
    objects = DownloadObjectStore()
    service = TransferService(ready, objects)

    capability = service.download_plan(ready.status.version.id, None)

    assert capability.item is None
    assert capability.next_cursor is None
    assert capability.complete is True
    assert objects.calls == []
    assert service.finalize(ready.status.version.id).state is VersionState.READY
    assert ready.finalize_calls == 1
