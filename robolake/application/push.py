"""Local-directory to immutable DatasetVersion push workflow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from robolake.application.contracts import (
    ProgressEvent,
    ProgressKind,
    PushResult,
    PutOutcome,
    ScannedFile,
)
from robolake.application.idempotency import make_idempotency_key
from robolake.application.ports import ByteTransferPort, ControlPlanePort, ScannerPort
from robolake.domain.errors import ContentConflictError, ManifestMismatchError, UploadConflictError
from robolake.domain.identifiers import DatasetName, RelativePath, Sha256Digest
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest
from robolake.domain.records import VersionRecord


class PushWorkflow:
    """Execute the complete M1 push publication as one vertical slice."""

    def __init__(
        self,
        scanner: ScannerPort,
        control_plane: ControlPlanePort,
        transfer: ByteTransferPort,
        progress: Callable[[ProgressEvent], None],
    ) -> None:
        self._scanner = scanner
        self._control_plane = control_plane
        self._transfer = transfer
        self._progress = progress

    def run(self, source: Path, dataset: DatasetName) -> PushResult:
        """Scan before persistence, resolve each unique Blob, then publish READY."""
        scanned = self._scanner.scan(source)
        self._emit(ProgressKind.SCANNED, None, 0, 0, scanned.manifest)
        dataset_key = make_idempotency_key(
            "dataset-create",
            [dataset.value.encode("utf-8")],
        )
        dataset_record = self._control_plane.create_dataset(dataset, dataset_key)
        version_key = make_idempotency_key(
            "version-register",
            [
                dataset_record.id.bytes,
                scanned.manifest.sha256.raw_bytes,
                scanned.manifest.canonical_bytes,
            ],
        )
        version = self._control_plane.register_version(
            dataset_record.id,
            scanned.manifest,
            version_key,
        )
        self._require_manifest(version.manifest_sha256, scanned.manifest.sha256)
        if version.state is VersionState.READY:
            return self._result(
                version,
                created_count=0,
                created_bytes=0,
                reused_count=scanned.manifest.unique_blob_count,
                reused_bytes=scanned.manifest.unique_blob_bytes,
            )

        sources: dict[Sha256Digest, ScannedFile] = {}
        for scanned_file in scanned.files:
            sources.setdefault(scanned_file.entry.sha256, scanned_file)

        resolved_count = 0
        resolved_bytes = 0
        created_count = 0
        created_bytes = 0
        reused_count = 0
        reused_bytes = 0
        for digest, scanned_file in sources.items():
            upload_key = make_idempotency_key(
                "upload-session-create",
                [version.id.bytes, digest.raw_bytes],
            )
            preparation = self._control_plane.prepare_upload(version.id, digest, upload_key)
            self._require_manifest(preparation.version.manifest_sha256, scanned.manifest.sha256)
            if preparation.size_bytes != scanned_file.entry.size_bytes:
                raise ContentConflictError("Prepared Blob size differs from the local manifest.")
            if preparation.available:
                reused_count += 1
                reused_bytes += preparation.size_bytes
                resolved_count += 1
                resolved_bytes += preparation.size_bytes
                self._emit(
                    ProgressKind.SKIPPED,
                    scanned_file.entry.relative_path,
                    resolved_count,
                    resolved_bytes,
                    scanned.manifest,
                )
                continue
            if preparation.session_id is None:
                raise ContentConflictError("Unavailable Blob has no upload session.")
            self._emit(
                ProgressKind.UPLOADING,
                scanned_file.entry.relative_path,
                resolved_count,
                resolved_bytes,
                scanned.manifest,
            )
            request = self._control_plane.upload_url(preparation.session_id)
            receipt = self._transfer.put(scanned_file.local_ref, request, scanned_file.entry)
            completed = self._control_plane.complete_upload(preparation.session_id, receipt.etag)
            self._require_manifest(completed.version.manifest_sha256, scanned.manifest.sha256)
            if not completed.available:
                raise UploadConflictError("Upload completion did not resolve the Blob.")
            if receipt.outcome is PutOutcome.CREATED:
                created_count += 1
                created_bytes += completed.size_bytes
            else:
                reused_count += 1
                reused_bytes += completed.size_bytes
            resolved_count += 1
            resolved_bytes += completed.size_bytes
            self._emit(
                ProgressKind.VERIFIED,
                scanned_file.entry.relative_path,
                resolved_count,
                resolved_bytes,
                scanned.manifest,
            )

        self._emit(
            ProgressKind.FINALIZING,
            None,
            resolved_count,
            resolved_bytes,
            scanned.manifest,
        )
        ready = self._control_plane.finalize(version.id)
        self._require_manifest(ready.manifest_sha256, scanned.manifest.sha256)
        if ready.state is not VersionState.READY:
            raise ContentConflictError("Finalized DatasetVersion did not become READY.")
        return self._result(
            ready,
            created_count=created_count,
            created_bytes=created_bytes,
            reused_count=reused_count,
            reused_bytes=reused_bytes,
        )

    @staticmethod
    def _require_manifest(actual: Sha256Digest, expected: Sha256Digest) -> None:
        if actual != expected:
            raise ManifestMismatchError("DatasetVersion manifest identity changed during push.")

    def _emit(
        self,
        kind: ProgressKind,
        relative_path: RelativePath | None,
        resolved_count: int,
        resolved_bytes: int,
        manifest: Manifest,
    ) -> None:
        self._progress(
            ProgressEvent(
                kind=kind,
                relative_path=relative_path,
                resolved_blob_count=resolved_count,
                unique_blob_count=manifest.unique_blob_count,
                resolved_blob_bytes=resolved_bytes,
                unique_blob_bytes=manifest.unique_blob_bytes,
            )
        )

    @staticmethod
    def _result(
        version: VersionRecord,
        *,
        created_count: int,
        created_bytes: int,
        reused_count: int,
        reused_bytes: int,
    ) -> PushResult:
        if (
            created_count + reused_count != version.unique_blob_count
            or created_bytes + reused_bytes != version.unique_blob_bytes
        ):
            raise ContentConflictError("Push outcome totals do not match unique content totals.")
        return PushResult(
            reference=version.reference,
            state=version.state,
            file_count=version.file_count,
            logical_bytes=version.logical_bytes,
            unique_blob_count=version.unique_blob_count,
            unique_blob_bytes=version.unique_blob_bytes,
            created_blob_count=created_count,
            created_blob_bytes=created_bytes,
            reused_blob_count=reused_count,
            reused_blob_bytes=reused_bytes,
        )
