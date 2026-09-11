"""Blob transfer reconciliation and one-capability download planning."""

from __future__ import annotations

import base64
import binascii
import hashlib
import struct
from uuid import UUID

from robolake.application.contracts import (
    DownloadCapability,
    DownloadItem,
    ObjectInfo,
    PresignedRequest,
    UploadPreparation,
)
from robolake.application.idempotency import request_fingerprint
from robolake.application.ports import ObjectStorePort, RegistryStore
from robolake.domain.errors import (
    IllegalTransitionError,
    InvalidCursorError,
    StoredObjectMismatchError,
    UploadConflictError,
)
from robolake.domain.identifiers import Sha256Digest, object_key_for
from robolake.domain.lifecycle import BlobState, FailureCode, UploadSessionState, VersionState
from robolake.domain.records import BlobRecord, VersionRecord

_CURSOR_FORMAT_VERSION = 1
_CURSOR_STRUCT = struct.Struct(">B16sQ")


class TransferService:
    """Coordinate persisted transfers without proxying file bytes."""

    def __init__(
        self,
        store: RegistryStore,
        objects: ObjectStorePort,
        *,
        presigned_url_ttl_seconds: int = 900,
        stream_chunk_bytes: int = 1_048_576,
    ) -> None:
        self._store = store
        self._objects = objects
        self._presigned_url_ttl_seconds = presigned_url_ttl_seconds
        self._stream_chunk_bytes = stream_chunk_bytes

    def prepare_upload(
        self, version_id: UUID, sha256: Sha256Digest, idempotency_key: str
    ) -> UploadPreparation:
        """Reuse an attested Blob or begin resolving a new one."""
        fingerprint = request_fingerprint([version_id.bytes, sha256.raw_bytes])
        context = self._store.prepare_upload(
            version_id,
            sha256,
            idempotency_key,
            fingerprint,
        )
        if context.blob.state is BlobState.AVAILABLE:
            return UploadPreparation(
                version=context.version,
                blob_sha256=context.blob.sha256,
                size_bytes=context.blob.size_bytes,
                available=True,
                session_id=None,
            )
        info = self._objects.head(context.blob.object_key)
        if info is None:
            if context.blob.state is BlobState.FAILED:
                context = self._store.restart_failed_upload(
                    version_id,
                    context.blob.id,
                    idempotency_key,
                    fingerprint,
                )
            return UploadPreparation(
                version=context.version,
                blob_sha256=context.blob.sha256,
                size_bytes=context.blob.size_bytes,
                available=False,
                session_id=context.session.id if context.session is not None else None,
            )
        session = context.session
        if session is None:
            raise NotImplementedError("Reconciliation requires a persisted session.")
        if session.state is UploadSessionState.CREATED:
            context = self._store.mark_upload_in_progress(session.id)
            session = context.session
            if session is None:
                raise NotImplementedError("Started upload lost its persisted session.")
        try:
            self._verify_object(context.blob, info)
        except StoredObjectMismatchError:
            self._store.mark_upload_failed(
                session.id,
                FailureCode.STORED_OBJECT_MISMATCH,
                "Stored object does not match its content address.",
            )
            raise
        verified = self._store.mark_upload_verified(session.id, info.etag)
        return UploadPreparation(
            version=verified.version,
            blob_sha256=verified.blob.sha256,
            size_bytes=verified.blob.size_bytes,
            available=True,
            session_id=verified.session.id if verified.session is not None else None,
        )

    def _verify_object(self, blob: BlobRecord, info: ObjectInfo) -> None:
        if info.size_bytes != blob.size_bytes:
            raise StoredObjectMismatchError("Stored object size does not match its Blob identity.")
        if info.checksum_sha256 is not None:
            if info.checksum_sha256 != blob.sha256:
                raise StoredObjectMismatchError(
                    "Stored object checksum does not match its Blob identity."
                )
            return
        digest = hashlib.sha256()
        total_bytes = 0
        for chunk in self._objects.iter_bytes(blob.object_key, self._stream_chunk_bytes):
            total_bytes += len(chunk)
            digest.update(chunk)
        if total_bytes != blob.size_bytes or digest.hexdigest() != blob.sha256.value:
            raise StoredObjectMismatchError("Stored object bytes do not match its Blob identity.")

    def issue_upload_url(self, session_id: UUID) -> PresignedRequest:
        """Start one persisted attempt and issue its exact create-only PUT capability."""
        context = self._store.get_upload(session_id)
        if context.session is None:
            raise NotImplementedError("Upload session does not exist.")
        if context.session.state is UploadSessionState.CREATED:
            context = self._store.mark_upload_in_progress(session_id)
        return self._objects.presign_put(
            context.blob.object_key,
            context.blob.size_bytes,
            context.blob.sha256.checksum_base64,
            self._presigned_url_ttl_seconds,
        )

    def upload_status(self, session_id: UUID) -> UploadPreparation:
        """Read one persisted file-level attempt without issuing a capability."""
        context = self._store.get_upload(session_id)
        return UploadPreparation(
            version=context.version,
            blob_sha256=context.blob.sha256,
            size_bytes=context.blob.size_bytes,
            available=context.blob.state is BlobState.AVAILABLE,
            session_id=context.session.id if context.session is not None else None,
        )

    def complete_upload(self, session_id: UUID, etag: str | None = None) -> UploadPreparation:
        """Reconcile one ambiguous conditional PUT outcome idempotently."""
        context = self._store.get_upload(session_id)
        info = self._objects.head(context.blob.object_key)
        if info is None:
            raise UploadConflictError("Upload outcome is not visible; rerun push.")
        try:
            self._verify_object(context.blob, info)
        except StoredObjectMismatchError:
            self._store.mark_upload_failed(
                session_id,
                FailureCode.STORED_OBJECT_MISMATCH,
                "Stored object does not match its content address.",
            )
            raise
        verified = self._store.mark_upload_verified(session_id, etag)
        return UploadPreparation(
            version=verified.version,
            blob_sha256=verified.blob.sha256,
            size_bytes=verified.blob.size_bytes,
            available=True,
            session_id=verified.session.id if verified.session is not None else None,
        )

    def finalize(self, version_id: UUID) -> VersionRecord:
        """Publish a fully resolved Version, replaying READY safely."""
        return self._store.finalize_version(version_id)

    def download_plan(self, version_id: UUID, cursor: str | None) -> DownloadCapability:
        """Issue at most one fresh GET capability for one immutable manifest ordinal."""
        status = self._store.get_status(version_id)
        if status.version.state is not VersionState.READY:
            raise IllegalTransitionError("Only READY DatasetVersions can be downloaded.")
        if status.version.file_count == 0:
            if cursor is not None:
                raise InvalidCursorError("Empty DatasetVersion has no download cursor.")
            return DownloadCapability(item=None, next_cursor=None, complete=True)

        ordinal = (
            0
            if cursor is None
            else decode_cursor(
                cursor,
                version_id,
                status.version.file_count,
            )
        )
        entry = self._store.get_download_entry(version_id, ordinal)
        if entry is None:
            raise InvalidCursorError("Download cursor does not resolve to a manifest entry.")
        next_ordinal = ordinal + 1
        next_cursor = (
            encode_cursor(version_id, next_ordinal)
            if next_ordinal < status.version.file_count
            else None
        )
        request = self._objects.presign_get(
            object_key_for(entry.sha256),
            self._presigned_url_ttl_seconds,
        )
        return DownloadCapability(
            item=DownloadItem(entry=entry, request=request),
            next_cursor=next_cursor,
            complete=next_cursor is None,
        )


def encode_cursor(version_id: UUID, manifest_ordinal: int) -> str:
    """Encode one fixed-width, Version-aware manifest ordinal."""
    if manifest_ordinal < 0:
        raise InvalidCursorError("Cursor ordinal must be nonnegative.")
    try:
        payload = _CURSOR_STRUCT.pack(
            _CURSOR_FORMAT_VERSION,
            version_id.bytes,
            manifest_ordinal,
        )
    except struct.error as error:
        raise InvalidCursorError("Cursor ordinal is outside the supported range.") from error
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str, version_id: UUID, file_count: int) -> int:
    """Strictly decode and validate one cursor for an immutable Version."""
    try:
        encoded = cursor.encode("ascii")
        padding = b"=" * (-len(encoded) % 4)
        payload = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        unpacked = _CURSOR_STRUCT.unpack(payload)
        format_version = int(unpacked[0])
        encoded_version = bytes(unpacked[1])
        ordinal = int(unpacked[2])
    except (UnicodeEncodeError, binascii.Error, struct.error, ValueError) as error:
        raise InvalidCursorError("Download cursor is malformed.") from error
    if base64.urlsafe_b64encode(payload).rstrip(b"=") != encoded:
        raise InvalidCursorError("Download cursor is not canonically encoded.")
    if format_version != _CURSOR_FORMAT_VERSION:
        raise InvalidCursorError("Download cursor format is unsupported.")
    if encoded_version != version_id.bytes:
        raise InvalidCursorError("Download cursor belongs to a different DatasetVersion.")
    if ordinal >= file_count:
        raise InvalidCursorError("Download cursor ordinal is outside this DatasetVersion.")
    return ordinal
