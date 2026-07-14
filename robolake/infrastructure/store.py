"""PostgreSQL implementation of the RoboLake registry port."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from robolake.application.contracts import UploadContext
from robolake.domain.errors import (
    AdmissionCapacityExhaustedError,
    AdmissionLeaseHeldError,
    AdmissionLeaseLostError,
    ContentConflictError,
    IdempotencyConflictError,
    IllegalTransitionError,
    InvalidPartNumberError,
    ManifestMismatchError,
    NotFoundError,
    PartChecksumRejectedError,
    PartSizeMismatchError,
)
from robolake.domain.identifiers import (
    DatasetName,
    DatasetReference,
    RelativePath,
    Sha256Digest,
    object_key_for,
)
from robolake.domain.lifecycle import (
    BlobState,
    FailureCode,
    UploadSessionState,
    VersionState,
)
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.multipart import (
    AdmissionLeaseEpoch,
    AdmissionLeaseOwnerId,
    CompletedPartReceipt,
    CompletionLeaseEpoch,
    CompletionLeaseOwnerId,
    CompletionPhase,
    MultipartInvocationId,
    MultipartSessionGeneration,
    MultipartSessionId,
    MultipartSessionState,
    PartAttribution,
    PartDefinition,
    PartPlan,
    UploadPartState,
)
from robolake.domain.records import (
    AcceptedCompletion,
    AdmissionLeaseRecord,
    BlobRecord,
    CompletionClaim,
    CompletionLeaseRecord,
    ContentStatus,
    DatasetRecord,
    HashedMultipartPlan,
    MultipartUploadContext,
    MultipartUploadSessionRecord,
    PartialCompletionEvidence,
    PartReconciliationResult,
    ProviderPartObservation,
    SnapshotStatus,
    UploadPartRecord,
    UploadSessionRecord,
    VerificationEvidence,
    VersionRecord,
    VersionStatus,
)
from robolake.infrastructure.models import (
    BlobModel,
    DatasetEntryModel,
    DatasetModel,
    DatasetVersionModel,
    IdempotencyRecordModel,
    MultipartAdmissionLeaseModel,
    MultipartCompletionLeaseModel,
    UploadPartModel,
    UploadSessionModel,
)


class SqlAlchemyStore:
    """Persist registry operations in short atomic SQLAlchemy units of work."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_dataset(
        self, name: DatasetName, idempotency_key: str, request_sha256: Sha256Digest
    ) -> DatasetRecord:
        """Create a Dataset or replay the response bound to a caller key."""
        with self._sessions.begin() as session:
            replay = session.get(
                IdempotencyRecordModel,
                {"scope": "dataset-create", "key": idempotency_key},
            )
            if replay is not None:
                if replay.request_sha256 != request_sha256.value:
                    raise IdempotencyConflictError(
                        "Idempotency key was already used for a different Dataset request."
                    )
                if replay.resource_id is None:
                    raise IdempotencyConflictError("Idempotency record has no Dataset resource.")
                model = session.get(DatasetModel, replay.resource_id)
                if model is None:
                    raise IdempotencyConflictError("Idempotency Dataset resource is unavailable.")
                return _dataset_record(model)

            model = session.scalar(select(DatasetModel).where(DatasetModel.name == name.value))
            if model is None:
                model = DatasetModel(name=name.value)
                session.add(model)
                session.flush()
            session.add(
                IdempotencyRecordModel(
                    scope="dataset-create",
                    key=idempotency_key,
                    request_sha256=request_sha256.value,
                    resource_type="dataset",
                    resource_id=model.id,
                    http_status=201,
                    response_json={"id": str(model.id), "name": model.name},
                )
            )
            return _dataset_record(model)

    def register_version(
        self,
        dataset_id: UUID,
        manifest: Manifest,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> VersionRecord:
        """Atomically seal a canonical manifest or replay its registered Version."""
        canonical = Manifest.from_canonical_bytes(manifest.canonical_bytes)
        if canonical.sha256 != manifest.sha256:
            raise ManifestMismatchError("Manifest digest does not match its canonical bytes.")

        with self._sessions.begin() as session:
            dataset = session.get(DatasetModel, dataset_id, with_for_update=True)
            if dataset is None:
                raise NotFoundError("Dataset does not exist.")

            replay = session.get(
                IdempotencyRecordModel,
                {"scope": "version-register", "key": idempotency_key},
            )
            if replay is not None:
                if replay.request_sha256 != request_sha256.value:
                    raise IdempotencyConflictError(
                        "Idempotency key was already used for a different Version request."
                    )
                if replay.resource_id is None:
                    raise IdempotencyConflictError("Idempotency record has no Version resource.")
                replayed = session.get(DatasetVersionModel, replay.resource_id)
                if replayed is None:
                    raise IdempotencyConflictError("Idempotency Version resource is unavailable.")
                return _version_record(replayed, dataset.name)

            existing = session.scalar(
                select(DatasetVersionModel).where(
                    DatasetVersionModel.dataset_id == dataset_id,
                    DatasetVersionModel.manifest_sha256 == canonical.sha256.value,
                )
            )
            if existing is None:
                version_number = session.scalar(
                    select(
                        func.coalesce(func.max(DatasetVersionModel.version_number), 0) + 1
                    ).where(DatasetVersionModel.dataset_id == dataset_id)
                )
                if version_number is None:
                    raise ContentConflictError("Could not allocate a DatasetVersion number.")
                existing = DatasetVersionModel(
                    dataset_id=dataset_id,
                    version_number=version_number,
                    manifest_schema_version=canonical.schema_version,
                    manifest_bytes=canonical.canonical_bytes,
                    manifest_sha256=canonical.sha256.value,
                    state=VersionState.DRAFT.value,
                    file_count=canonical.file_count,
                    logical_bytes=canonical.logical_bytes,
                    unique_blob_count=canonical.unique_blob_count,
                    unique_blob_bytes=canonical.unique_blob_bytes,
                    sealed_at=None,
                )
                session.add(existing)
                session.flush()
                for ordinal, entry in enumerate(canonical.entries):
                    blob = self._get_or_create_blob(session, entry.sha256, entry.size_bytes)
                    session.add(
                        DatasetEntryModel(
                            dataset_version_id=existing.id,
                            manifest_ordinal=ordinal,
                            relative_path=entry.relative_path.value,
                            blob_id=blob.id,
                        )
                    )
                session.flush()
                existing.sealed_at = datetime.now(UTC)
                session.flush()

            session.add(
                IdempotencyRecordModel(
                    scope="version-register",
                    key=idempotency_key,
                    request_sha256=request_sha256.value,
                    resource_type="dataset-version",
                    resource_id=existing.id,
                    http_status=201,
                    response_json={
                        "id": str(existing.id),
                        "dataset_id": str(dataset_id),
                        "version_number": existing.version_number,
                    },
                )
            )
            return _version_record(existing, dataset.name)

    def resolve_version(self, reference: DatasetReference) -> VersionRecord:
        """Resolve a Dataset name and registration-order number."""
        with self._sessions() as session:
            row = session.execute(
                select(DatasetVersionModel, DatasetModel.name)
                .join(DatasetModel, DatasetModel.id == DatasetVersionModel.dataset_id)
                .where(
                    DatasetModel.name == reference.dataset.value,
                    DatasetVersionModel.version_number == reference.version_number,
                )
            ).one_or_none()
            if row is None:
                raise NotFoundError("DatasetVersion does not exist.")
            version, dataset_name = row
            return _version_record(version, dataset_name)

    def get_manifest(self, version_id: UUID) -> Manifest:
        """Read canonical bytes for independent application validation."""
        with self._sessions() as session:
            manifest_bytes = session.scalar(
                select(DatasetVersionModel.manifest_bytes).where(
                    DatasetVersionModel.id == version_id
                )
            )
            if manifest_bytes is None:
                raise NotFoundError("DatasetVersion does not exist.")
            return Manifest.from_canonical_bytes(manifest_bytes)

    def get_status(self, version_id: UUID) -> VersionStatus:
        """Derive progress from immutable entries and current Blob attestations."""
        with self._sessions() as session:
            row = session.execute(
                select(DatasetVersionModel, DatasetModel.name)
                .join(DatasetModel, DatasetModel.id == DatasetVersionModel.dataset_id)
                .where(DatasetVersionModel.id == version_id)
            ).one_or_none()
            if row is None:
                raise NotFoundError("DatasetVersion does not exist.")
            model, dataset_name = row

            ready_file_count, ready_logical_bytes = session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(BlobModel.size_bytes), 0),
                )
                .select_from(DatasetEntryModel)
                .join(BlobModel, BlobModel.id == DatasetEntryModel.blob_id)
                .where(
                    DatasetEntryModel.dataset_version_id == version_id,
                    BlobModel.state == BlobState.AVAILABLE.value,
                )
            ).one()
            available_blobs = (
                select(BlobModel.id, BlobModel.size_bytes)
                .join(DatasetEntryModel, DatasetEntryModel.blob_id == BlobModel.id)
                .where(
                    DatasetEntryModel.dataset_version_id == version_id,
                    BlobModel.state == BlobState.AVAILABLE.value,
                )
                .distinct()
                .subquery()
            )
            available_blob_count, available_blob_bytes = session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(available_blobs.c.size_bytes), 0),
                ).select_from(available_blobs)
            ).one()
            blocking_failure = session.scalar(
                select(BlobModel.failure_code)
                .join(DatasetEntryModel, DatasetEntryModel.blob_id == BlobModel.id)
                .where(
                    DatasetEntryModel.dataset_version_id == version_id,
                    BlobModel.failure_code.is_not(None),
                )
                .limit(1)
            )
            version = _version_record(model, dataset_name)
            return VersionStatus(
                version=version,
                snapshot=SnapshotStatus(
                    file_count=version.file_count,
                    logical_bytes=version.logical_bytes,
                    ready_file_count=ready_file_count,
                    ready_logical_bytes=ready_logical_bytes,
                ),
                content=ContentStatus(
                    unique_blob_count=version.unique_blob_count,
                    unique_blob_bytes=version.unique_blob_bytes,
                    available_blob_count=available_blob_count,
                    available_blob_bytes=available_blob_bytes,
                ),
                blocking_failure_code=(
                    FailureCode(blocking_failure) if blocking_failure is not None else None
                ),
            )

    def get_download_entry(self, version_id: UUID, manifest_ordinal: int) -> ManifestEntry | None:
        """Resolve one immutable entry by persisted canonical ordinal."""
        with self._sessions() as session:
            row = session.execute(
                select(
                    DatasetEntryModel.relative_path,
                    BlobModel.size_bytes,
                    BlobModel.sha256,
                )
                .join(BlobModel, BlobModel.id == DatasetEntryModel.blob_id)
                .where(
                    DatasetEntryModel.dataset_version_id == version_id,
                    DatasetEntryModel.manifest_ordinal == manifest_ordinal,
                )
            ).one_or_none()
            if row is None:
                return None
            relative_path, size_bytes, sha256 = row
            return ManifestEntry(
                relative_path=RelativePath.parse(relative_path),
                size_bytes=size_bytes,
                sha256=Sha256Digest.parse(sha256),
            )

    def prepare_upload(
        self,
        version_id: UUID,
        sha256: Sha256Digest,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> UploadContext:
        """Create or reuse one file-level upload attempt for referenced content."""
        with self._sessions.begin() as session:
            version = session.get(DatasetVersionModel, version_id, with_for_update=True)
            if version is None:
                raise NotFoundError("DatasetVersion does not exist.")
            dataset = session.get(DatasetModel, version.dataset_id)
            if dataset is None:
                raise NotFoundError("Dataset does not exist.")
            blob = session.scalar(
                select(BlobModel)
                .join(DatasetEntryModel, DatasetEntryModel.blob_id == BlobModel.id)
                .where(
                    DatasetEntryModel.dataset_version_id == version_id,
                    BlobModel.sha256 == sha256.value,
                )
                .with_for_update()
            )
            if blob is None:
                raise NotFoundError("Blob is not referenced by this DatasetVersion.")
            if blob.state == BlobState.AVAILABLE.value:
                return _upload_context(version, dataset.name, blob, None)

            replay = session.get(
                IdempotencyRecordModel,
                {"scope": "upload-session-create", "key": idempotency_key},
            )
            if replay is not None:
                if replay.request_sha256 != request_sha256.value:
                    raise IdempotencyConflictError(
                        "Idempotency key was already used for a different upload request."
                    )
                if replay.resource_id is None:
                    raise IdempotencyConflictError("Idempotency record has no upload session.")
                upload = session.get(UploadSessionModel, replay.resource_id)
                if upload is None:
                    raise IdempotencyConflictError("Idempotency upload session is unavailable.")
                return _upload_context(version, dataset.name, blob, upload)

            upload = session.scalar(
                select(UploadSessionModel)
                .where(
                    UploadSessionModel.blob_id == blob.id,
                    UploadSessionModel.state.in_(
                        [UploadSessionState.CREATED.value, UploadSessionState.IN_PROGRESS.value]
                    ),
                )
                .with_for_update()
            )
            if upload is None and blob.state == BlobState.PENDING.value:
                upload = UploadSessionModel(
                    blob_id=blob.id,
                    initiating_version_id=version_id,
                    strategy="SINGLE_PUT",
                    state=UploadSessionState.CREATED.value,
                )
                session.add(upload)
                session.flush()
            if upload is None:
                upload = session.scalar(
                    select(UploadSessionModel)
                    .where(UploadSessionModel.blob_id == blob.id)
                    .order_by(UploadSessionModel.created_at.desc())
                    .limit(1)
                )
            if upload is None:
                raise ContentConflictError("Blob state has no recoverable upload session.")
            session.add(
                IdempotencyRecordModel(
                    scope="upload-session-create",
                    key=idempotency_key,
                    request_sha256=request_sha256.value,
                    resource_type="upload-session",
                    resource_id=upload.id,
                    http_status=200,
                    response_json={"id": str(upload.id)},
                )
            )
            return _upload_context(version, dataset.name, blob, upload)

    def get_upload(self, session_id: UUID) -> UploadContext:
        """Read one upload attempt and its initiating publication context."""
        with self._sessions() as session:
            upload = session.get(UploadSessionModel, session_id)
            if upload is None:
                raise NotFoundError("UploadSession does not exist.")
            return self._load_upload_context(session, upload)

    def mark_upload_in_progress(self, session_id: UUID) -> UploadContext:
        """Atomically start a CREATED single-PUT attempt."""
        with self._sessions.begin() as session:
            upload = session.get(UploadSessionModel, session_id, with_for_update=True)
            if upload is None:
                raise NotFoundError("UploadSession does not exist.")
            blob = session.get(BlobModel, upload.blob_id, with_for_update=True)
            version = session.get(
                DatasetVersionModel,
                upload.initiating_version_id,
                with_for_update=True,
            )
            if blob is None or version is None:
                raise ContentConflictError("UploadSession registry context is incomplete.")
            if upload.state == UploadSessionState.CREATED.value:
                if blob.state == BlobState.PENDING.value:
                    blob.state = BlobState.UPLOADING.value
                if version.state in {VersionState.DRAFT.value, VersionState.FAILED.value}:
                    version.state = VersionState.UPLOADING.value
                    version.failure_code = None
                    version.failure_detail = None
                upload.state = UploadSessionState.IN_PROGRESS.value
                upload.last_activity_at = datetime.now(UTC)
                session.flush()
            elif upload.state != UploadSessionState.IN_PROGRESS.value:
                raise IllegalTransitionError("Only a CREATED upload session can be started.")
            dataset = session.get(DatasetModel, version.dataset_id)
            if dataset is None:
                raise ContentConflictError("UploadSession Dataset is unavailable.")
            return _upload_context(version, dataset.name, blob, upload)

    def mark_upload_verified(self, session_id: UUID, etag: str | None) -> UploadContext:
        """Atomically attest one Blob and complete its active attempt."""
        with self._sessions.begin() as session:
            upload = session.get(UploadSessionModel, session_id, with_for_update=True)
            if upload is None:
                raise NotFoundError("UploadSession does not exist.")
            blob = session.get(BlobModel, upload.blob_id, with_for_update=True)
            version = session.get(DatasetVersionModel, upload.initiating_version_id)
            if blob is None or version is None:
                raise ContentConflictError("UploadSession registry context is incomplete.")
            if upload.state == UploadSessionState.COMPLETED.value:
                dataset = session.get(DatasetModel, version.dataset_id)
                if dataset is None:
                    raise ContentConflictError("UploadSession Dataset is unavailable.")
                return _upload_context(version, dataset.name, blob, upload)
            if upload.state != UploadSessionState.IN_PROGRESS.value:
                raise IllegalTransitionError("Only an IN_PROGRESS upload can be verified.")
            if blob.state != BlobState.UPLOADING.value:
                raise IllegalTransitionError("Only an UPLOADING Blob can be verified.")
            now = datetime.now(UTC)
            blob.state = BlobState.VERIFYING.value
            session.flush()
            blob.state = BlobState.AVAILABLE.value
            blob.verified_at = now
            upload.state = UploadSessionState.COMPLETED.value
            upload.etag = etag
            upload.completed_at = now
            upload.last_activity_at = now
            session.flush()
            dataset = session.get(DatasetModel, version.dataset_id)
            if dataset is None:
                raise ContentConflictError("UploadSession Dataset is unavailable.")
            return _upload_context(version, dataset.name, blob, upload)

    def mark_upload_failed(self, session_id: UUID, code: FailureCode, detail: str) -> UploadContext:
        """Persist a proven integrity failure without deleting or overwriting its object."""
        with self._sessions.begin() as session:
            upload = session.get(UploadSessionModel, session_id, with_for_update=True)
            if upload is None:
                raise NotFoundError("UploadSession does not exist.")
            blob = session.get(BlobModel, upload.blob_id, with_for_update=True)
            version = session.get(
                DatasetVersionModel,
                upload.initiating_version_id,
                with_for_update=True,
            )
            if blob is None or version is None:
                raise ContentConflictError("UploadSession registry context is incomplete.")
            if upload.state == UploadSessionState.FAILED.value:
                dataset = session.get(DatasetModel, version.dataset_id)
                if dataset is None:
                    raise ContentConflictError("UploadSession Dataset is unavailable.")
                return _upload_context(version, dataset.name, blob, upload)
            if (
                upload.state != UploadSessionState.IN_PROGRESS.value
                or blob.state != BlobState.UPLOADING.value
                or version.state != VersionState.UPLOADING.value
            ):
                raise IllegalTransitionError("Only an active publication can record failure.")
            now = datetime.now(UTC)
            blob.state = BlobState.VERIFYING.value
            version.state = VersionState.VERIFYING.value
            version.verifying_at = now
            session.flush()
            blob.state = BlobState.FAILED.value
            blob.failure_code = code.value
            blob.failure_detail = detail
            version.state = VersionState.FAILED.value
            version.failure_code = code.value
            version.failure_detail = detail
            upload.state = UploadSessionState.FAILED.value
            upload.failure_code = code.value
            upload.failure_detail = detail
            upload.completed_at = now
            upload.last_activity_at = now
            session.flush()
            dataset = session.get(DatasetModel, version.dataset_id)
            if dataset is None:
                raise ContentConflictError("UploadSession Dataset is unavailable.")
            return _upload_context(version, dataset.name, blob, upload)

    def restart_failed_upload(
        self,
        version_id: UUID,
        blob_id: UUID,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> UploadContext:
        """Restart the same failed identity after external cleanup was explicitly proven."""
        with self._sessions.begin() as session:
            version = session.get(DatasetVersionModel, version_id, with_for_update=True)
            blob = session.get(BlobModel, blob_id, with_for_update=True)
            if version is None or blob is None:
                raise NotFoundError("Failed upload context does not exist.")
            dataset = session.get(DatasetModel, version.dataset_id)
            if dataset is None:
                raise ContentConflictError("DatasetVersion Dataset is unavailable.")
            referenced = session.scalar(
                select(func.count())
                .select_from(DatasetEntryModel)
                .where(
                    DatasetEntryModel.dataset_version_id == version_id,
                    DatasetEntryModel.blob_id == blob_id,
                )
            )
            if not referenced:
                raise NotFoundError("Blob is not referenced by this DatasetVersion.")
            replay = session.get(
                IdempotencyRecordModel,
                {"scope": "upload-session-create", "key": idempotency_key},
            )
            if replay is not None:
                if replay.request_sha256 != request_sha256.value or replay.resource_id is None:
                    raise IdempotencyConflictError(
                        "Idempotency key was already used for a different upload request."
                    )
                prior = session.get(UploadSessionModel, replay.resource_id)
                if prior is None:
                    raise IdempotencyConflictError("Idempotency upload session is unavailable.")
                return _upload_context(version, dataset.name, blob, prior)
            if version.state != VersionState.FAILED.value or blob.state != BlobState.FAILED.value:
                raise IllegalTransitionError("Only a failed Blob and Version can be restarted.")
            version.state = VersionState.UPLOADING.value
            version.failure_code = None
            version.failure_detail = None
            blob.state = BlobState.UPLOADING.value
            blob.failure_code = None
            blob.failure_detail = None
            session.flush()
            upload = UploadSessionModel(
                blob_id=blob_id,
                initiating_version_id=version_id,
                strategy="SINGLE_PUT",
                state=UploadSessionState.CREATED.value,
            )
            session.add(upload)
            session.flush()
            session.add(
                IdempotencyRecordModel(
                    scope="upload-session-create",
                    key=idempotency_key,
                    request_sha256=request_sha256.value,
                    resource_type="upload-session",
                    resource_id=upload.id,
                    http_status=200,
                    response_json={"id": str(upload.id)},
                )
            )
            return _upload_context(version, dataset.name, blob, upload)

    def finalize_version(self, version_id: UUID) -> VersionRecord:
        """Publish a sealed Version after recounting every invariant and Blob attestation."""
        with self._sessions.begin() as session:
            version = session.get(DatasetVersionModel, version_id, with_for_update=True)
            if version is None:
                raise NotFoundError("DatasetVersion does not exist.")
            dataset = session.get(DatasetModel, version.dataset_id)
            if dataset is None:
                raise ContentConflictError("DatasetVersion Dataset is unavailable.")
            if version.state == VersionState.READY.value:
                return _version_record(version, dataset.name)
            if version.state not in {VersionState.DRAFT.value, VersionState.UPLOADING.value}:
                raise IllegalTransitionError("DatasetVersion cannot be finalized from its state.")
            canonical = Manifest.from_canonical_bytes(version.manifest_bytes)
            if (
                canonical.sha256.value != version.manifest_sha256
                or canonical.file_count != version.file_count
                or canonical.logical_bytes != version.logical_bytes
                or canonical.unique_blob_count != version.unique_blob_count
                or canonical.unique_blob_bytes != version.unique_blob_bytes
            ):
                raise ManifestMismatchError("Stored manifest summaries are inconsistent.")
            unavailable = session.scalar(
                select(func.count())
                .select_from(DatasetEntryModel)
                .join(BlobModel, BlobModel.id == DatasetEntryModel.blob_id)
                .where(
                    DatasetEntryModel.dataset_version_id == version_id,
                    BlobModel.state != BlobState.AVAILABLE.value,
                )
            )
            if unavailable:
                raise IllegalTransitionError(
                    "Every referenced Blob must be AVAILABLE before READY."
                )
            now = datetime.now(UTC)
            version.state = VersionState.VERIFYING.value
            version.verifying_at = now
            session.flush()
            version.state = VersionState.READY.value
            version.ready_at = now
            session.flush()
            return _version_record(version, dataset.name)

    def create_or_resolve_multipart(
        self,
        request_id: UUID,
        version_id: UUID,
        blob_id: UUID,
        plan: HashedMultipartPlan,
    ) -> MultipartUploadContext:
        """Create one generation-numbered plan or replay its immutable binding."""
        request_sha256 = _multipart_request_sha256(
            "multipart-session-resolve",
            str(version_id),
            str(blob_id),
            plan.sha256.value,
        )
        key = str(request_id)
        with self._sessions.begin() as session:
            replay = session.get(
                IdempotencyRecordModel,
                {"scope": "multipart-session-resolve", "key": key},
            )
            if replay is not None:
                return self._replay_multipart_context(session, replay, request_sha256)

            blob = session.get(BlobModel, blob_id, with_for_update=True)
            version = session.get(DatasetVersionModel, version_id)
            if blob is None or version is None:
                raise NotFoundError("Multipart Blob or initiating DatasetVersion does not exist.")
            is_referenced = session.scalar(
                select(func.count())
                .select_from(DatasetEntryModel)
                .where(
                    DatasetEntryModel.dataset_version_id == version_id,
                    DatasetEntryModel.blob_id == blob_id,
                )
            )
            if not is_referenced:
                raise NotFoundError("Blob is not referenced by the initiating DatasetVersion.")
            if blob.size_bytes != plan.plan.blob_size_bytes:
                raise ContentConflictError("PartPlan size differs from immutable Blob size.")

            active_states = tuple(
                state.value
                for state in (
                    MultipartSessionState.CREATED,
                    MultipartSessionState.INITIATING,
                    MultipartSessionState.IN_PROGRESS,
                    MultipartSessionState.COMPLETING,
                    MultipartSessionState.ABORTING,
                )
            )
            upload = session.scalar(
                select(UploadSessionModel)
                .where(
                    UploadSessionModel.blob_id == blob_id,
                    UploadSessionModel.state.in_(active_states),
                )
                .with_for_update()
            )
            if upload is not None:
                if upload.strategy != "MULTIPART" or upload.part_plan_sha256 != plan.sha256.value:
                    raise ContentConflictError(
                        "Active upload does not match the deterministic plan."
                    )
            else:
                generation = session.scalar(
                    select(
                        func.coalesce(func.max(UploadSessionModel.session_generation), 0) + 1
                    ).where(UploadSessionModel.blob_id == blob_id)
                )
                if generation is None:
                    raise ContentConflictError("Could not allocate a multipart generation.")
                now = datetime.now(UTC)
                upload = UploadSessionModel(
                    blob_id=blob_id,
                    initiating_version_id=version_id,
                    strategy="MULTIPART",
                    state=MultipartSessionState.CREATED.value,
                    session_generation=generation,
                    part_size_bytes=plan.plan.part_size_bytes,
                    planned_part_count=plan.plan.part_count,
                    part_plan_schema_version=plan.plan.schema_version,
                    part_plan_sha256=plan.sha256.value,
                    created_at=now,
                    last_activity_at=now,
                )
                session.add(upload)
                session.flush()
                session.add_all(
                    UploadPartModel(
                        upload_session_id=upload.id,
                        part_number=part.part_number,
                        offset_bytes=part.offset_bytes,
                        size_bytes=part.size_bytes,
                        sha256=part.expected_sha256.value,
                        state=UploadPartState.PENDING.value,
                        capability_issue_count=0,
                        created_at=now,
                    )
                    for part in plan.plan.parts
                )
                session.flush()

            session.add(
                IdempotencyRecordModel(
                    scope="multipart-session-resolve",
                    key=key,
                    request_sha256=request_sha256,
                    resource_type="upload-session",
                    resource_id=upload.id,
                    http_status=200,
                    response_json={"session_id": str(upload.id)},
                )
            )
            session.flush()
            return self._load_multipart_context(session, upload)

    def get_multipart(self, session_id: UUID) -> MultipartUploadContext:
        """Read one multipart attempt and its frozen PartPlan."""
        with self._sessions() as session:
            upload = session.get(UploadSessionModel, session_id)
            if upload is None or upload.strategy != "MULTIPART":
                raise NotFoundError("Multipart session does not exist.")
            return self._load_multipart_context(session, upload)

    def acquire_multipart_lease(
        self,
        request_id: UUID,
        invocation_id: UUID,
        session_id: UUID,
        ttl_seconds: int,
        capacity: int,
    ) -> AdmissionLeaseRecord:
        """Acquire, renew, or take over upload admission under one global DB fence."""
        if ttl_seconds <= 0 or capacity <= 0:
            raise ContentConflictError("Lease TTL and admission capacity must be positive.")
        request_sha256 = _multipart_request_sha256(
            "multipart-admission-acquire",
            str(session_id),
            str(invocation_id),
            str(ttl_seconds),
            str(capacity),
        )
        key = str(request_id)
        with self._sessions.begin() as session:
            replay = session.get(
                IdempotencyRecordModel,
                {"scope": "multipart-admission-acquire", "key": key},
            )
            if replay is not None:
                return _replay_admission_lease(replay, request_sha256)

            session.execute(text("SELECT pg_advisory_xact_lock(782341902115)"))
            upload = session.get(UploadSessionModel, session_id, with_for_update=True)
            if upload is None or upload.strategy != "MULTIPART":
                raise NotFoundError("Multipart session does not exist.")
            if upload.state in {
                MultipartSessionState.COMPLETED.value,
                MultipartSessionState.ABORTED.value,
                MultipartSessionState.CANCELLED.value,
                MultipartSessionState.FAILED.value,
            }:
                raise IllegalTransitionError("Terminal multipart session cannot acquire admission.")

            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise ContentConflictError("Database time is unavailable.")
            lease = session.get(MultipartAdmissionLeaseModel, session_id, with_for_update=True)
            if lease is not None and lease.expires_at > now:
                if lease.invocation_id != invocation_id:
                    raise AdmissionLeaseHeldError("Another invocation owns multipart admission.")
                lease.renewed_at = now
                lease.expires_at = now + timedelta(seconds=ttl_seconds)
            else:
                active_count = session.scalar(
                    select(func.count())
                    .select_from(MultipartAdmissionLeaseModel)
                    .where(MultipartAdmissionLeaseModel.expires_at > now)
                )
                if active_count is None or active_count >= capacity:
                    raise AdmissionCapacityExhaustedError(
                        "Multipart admission capacity is exhausted."
                    )
                owner_id = uuid4()
                if lease is None:
                    lease = MultipartAdmissionLeaseModel(
                        upload_session_id=session_id,
                        invocation_id=invocation_id,
                        owner_id=owner_id,
                        epoch=1,
                        acquired_at=now,
                        renewed_at=now,
                        expires_at=now + timedelta(seconds=ttl_seconds),
                    )
                    session.add(lease)
                else:
                    lease.invocation_id = invocation_id
                    lease.owner_id = owner_id
                    lease.epoch += 1
                    lease.acquired_at = now
                    lease.renewed_at = now
                    lease.expires_at = now + timedelta(seconds=ttl_seconds)
            session.flush()
            result = _admission_lease_record(lease)
            session.add(
                IdempotencyRecordModel(
                    scope="multipart-admission-acquire",
                    key=key,
                    request_sha256=request_sha256,
                    resource_type="upload-session",
                    resource_id=session_id,
                    http_status=200,
                    response_json={
                        "session_id": str(session_id),
                        "invocation_id": str(invocation_id),
                        "owner_id": str(result.lease_owner_id.value),
                        "epoch": result.lease_epoch.value,
                        "expires_at": result.expires_at.isoformat(),
                        "renewed_at": result.last_renewed_at.isoformat(),
                    },
                )
            )
            return result

    def release_multipart_lease(self, session_id: UUID, owner_id: UUID, epoch: int) -> None:
        """End current upload admission without deleting its fencing history."""
        with self._sessions.begin() as session:
            released = session.execute(
                text(
                    "UPDATE multipart_admission_leases "
                    "SET renewed_at = clock_timestamp(), expires_at = clock_timestamp() "
                    "WHERE upload_session_id = :session_id AND owner_id = :owner_id "
                    "AND epoch = :epoch AND expires_at > clock_timestamp() RETURNING upload_session_id"
                ),
                {"session_id": session_id, "owner_id": owner_id, "epoch": epoch},
            ).scalar_one_or_none()
            if released is None:
                raise AdmissionLeaseLostError("Multipart admission lease is no longer current.")

    def begin_provider_initiation(self, session_id: UUID, owner_id: UUID, epoch: int) -> None:
        """Commit initiation intent under the current upload fence."""
        with self._sessions.begin() as session:
            changed = session.execute(
                text(
                    "UPDATE upload_sessions SET state = 'INITIATING', "
                    "initiation_started_at = clock_timestamp(), last_activity_at = clock_timestamp() "
                    "WHERE id = :session_id AND state = 'CREATED' AND EXISTS ("
                    "SELECT 1 FROM multipart_admission_leases lease "
                    "WHERE lease.upload_session_id = upload_sessions.id "
                    "AND lease.owner_id = :owner_id AND lease.epoch = :epoch "
                    "AND lease.expires_at > clock_timestamp()) RETURNING id"
                ),
                {"session_id": session_id, "owner_id": owner_id, "epoch": epoch},
            ).scalar_one_or_none()
            if changed is None:
                raise AdmissionLeaseLostError("Current admission fence could not begin initiation.")

    def record_provider_upload(
        self, session_id: UUID, owner_id: UUID, epoch: int, provider_upload_id: str
    ) -> None:
        """Persist the opaque provider attempt ID under the current upload fence."""
        if not provider_upload_id:
            raise ContentConflictError("Provider upload ID must not be empty.")
        with self._sessions.begin() as session:
            blob_id = session.execute(
                text(
                    "UPDATE upload_sessions SET state = 'IN_PROGRESS', "
                    "provider_upload_id = :provider_upload_id, last_activity_at = clock_timestamp() "
                    "WHERE id = :session_id AND state = 'INITIATING' AND EXISTS ("
                    "SELECT 1 FROM multipart_admission_leases lease "
                    "WHERE lease.upload_session_id = upload_sessions.id "
                    "AND lease.owner_id = :owner_id AND lease.epoch = :epoch "
                    "AND lease.expires_at > clock_timestamp()) RETURNING blob_id"
                ),
                {
                    "session_id": session_id,
                    "owner_id": owner_id,
                    "epoch": epoch,
                    "provider_upload_id": provider_upload_id,
                },
            ).scalar_one_or_none()
            if blob_id is None:
                raise AdmissionLeaseLostError(
                    "Current admission fence could not record initiation."
                )
            blob = session.get(BlobModel, blob_id, with_for_update=True)
            if blob is None:
                raise ContentConflictError("Multipart Blob is unavailable.")
            if blob.state == BlobState.PENDING.value:
                blob.state = BlobState.UPLOADING.value
                session.flush()
            elif blob.state != BlobState.UPLOADING.value:
                raise IllegalTransitionError("Multipart initiation requires a pending Blob.")

    def record_uploaded_part(
        self,
        session_id: UUID,
        owner_id: UUID,
        epoch: int,
        receipt: CompletedPartReceipt,
    ) -> UploadPartRecord:
        """Persist one successful UploadPart response under the current upload fence."""
        with self._sessions.begin() as session:
            current = session.execute(
                text(
                    "UPDATE upload_sessions SET last_activity_at = clock_timestamp() "
                    "WHERE id = :session_id AND state = 'IN_PROGRESS' AND EXISTS ("
                    "SELECT 1 FROM multipart_admission_leases lease "
                    "WHERE lease.upload_session_id = upload_sessions.id "
                    "AND lease.owner_id = :owner_id AND lease.epoch = :epoch "
                    "AND lease.expires_at > clock_timestamp()) RETURNING id"
                ),
                {"session_id": session_id, "owner_id": owner_id, "epoch": epoch},
            ).scalar_one_or_none()
            if current is None:
                raise AdmissionLeaseLostError("Stale owner cannot confirm an uploaded Part.")
            part = session.get(
                UploadPartModel,
                {"upload_session_id": session_id, "part_number": receipt.part_number},
                with_for_update=True,
            )
            if part is None:
                raise InvalidPartNumberError("Part is not present in the frozen PartPlan.")
            expected = Sha256Digest.parse(part.sha256)
            receipt.validate_for(expected)
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise ContentConflictError("Database time is unavailable.")
            if part.state == UploadPartState.VERIFIED.value:
                existing = _upload_part_record(part).response_receipt
                if existing != receipt:
                    raise ContentConflictError("VERIFIED Part receipt is immutable.")
                return _upload_part_record(part)
            part.state = UploadPartState.UPLOADED.value
            part.upload_response_etag = receipt.response_etag
            part.upload_response_checksum_sha256_base64 = receipt.response_checksum_sha256_base64
            part.upload_response_received_at = now
            part.uploaded_at = now
            session.flush()
            return _upload_part_record(part)

    def reconcile_parts(
        self,
        session_id: UUID,
        owner_id: UUID,
        epoch: int,
        provider_parts: Sequence[ProviderPartObservation],
        attribution: PartAttribution,
    ) -> PartReconciliationResult:
        """Reconcile one complete ListParts observation without manufacturing receipts."""
        observations = {part.part_number: part for part in provider_parts}
        if len(observations) != len(provider_parts):
            raise ContentConflictError("Provider Part observations contain duplicate numbers.")
        with self._sessions.begin() as session:
            current = session.execute(
                text(
                    "UPDATE upload_sessions SET last_provider_reconciled_at = clock_timestamp(), "
                    "last_activity_at = clock_timestamp() WHERE id = :session_id "
                    "AND state = 'IN_PROGRESS' AND EXISTS (SELECT 1 "
                    "FROM multipart_admission_leases lease "
                    "WHERE lease.upload_session_id = upload_sessions.id "
                    "AND lease.owner_id = :owner_id AND lease.epoch = :epoch "
                    "AND lease.expires_at > clock_timestamp()) RETURNING id"
                ),
                {"session_id": session_id, "owner_id": owner_id, "epoch": epoch},
            ).scalar_one_or_none()
            if current is None:
                raise AdmissionLeaseLostError("Stale owner cannot reconcile multipart Parts.")
            models = session.scalars(
                select(UploadPartModel)
                .where(UploadPartModel.upload_session_id == session_id)
                .order_by(UploadPartModel.part_number)
                .with_for_update()
            ).all()
            planned_numbers = {part.part_number for part in models}
            unknown_numbers = set(observations) - planned_numbers
            if unknown_numbers:
                raise InvalidPartNumberError(
                    "Provider returned a Part outside the frozen PartPlan."
                )
            reconciled_at = session.scalar(select(func.clock_timestamp()))
            if not isinstance(reconciled_at, datetime):
                raise ContentConflictError("Database time is unavailable.")

            candidates: list[UploadPartModel] = []
            for part in models:
                observation = observations.get(part.part_number)
                if not _apply_provider_part_observation(part, observation, reconciled_at):
                    if part.state != UploadPartState.PENDING.value:
                        part.state = UploadPartState.PENDING.value
                else:
                    if part.state == UploadPartState.PENDING.value:
                        part.state = UploadPartState.UPLOADED.value
                    candidates.append(part)
            session.flush()
            for part in candidates:
                part.state = UploadPartState.VERIFIED.value
                if part.provider_listed_at is None:
                    raise ContentConflictError("VERIFIED Part lacks provider observation time.")
                part.verified_at = part.provider_listed_at
            session.flush()
            resolved = [part for part in models if part.state == UploadPartState.VERIFIED.value]
            return PartReconciliationResult(
                attribution=attribution,
                resolved_part_count=len(resolved),
                resolved_part_bytes=sum(part.size_bytes for part in resolved),
            )

    def mark_multipart_initiation_ambiguous(
        self, session_id: UUID, owner_id: UUID, epoch: int
    ) -> None:
        """Terminalize ambiguous initiation and release capacity atomically."""
        with self._sessions.begin() as session:
            changed = session.execute(
                text(
                    "UPDATE upload_sessions SET state = 'FAILED', "
                    "failure_code = 'INITIATION_AMBIGUOUS', "
                    "initiation_ambiguous_at = clock_timestamp(), completed_at = clock_timestamp(), "
                    "last_activity_at = clock_timestamp() "
                    "WHERE id = :session_id AND state = 'INITIATING' AND EXISTS ("
                    "SELECT 1 FROM multipart_admission_leases lease "
                    "WHERE lease.upload_session_id = upload_sessions.id "
                    "AND lease.owner_id = :owner_id AND lease.epoch = :epoch "
                    "AND lease.expires_at > clock_timestamp()) RETURNING id"
                ),
                {"session_id": session_id, "owner_id": owner_id, "epoch": epoch},
            ).scalar_one_or_none()
            if changed is None:
                raise AdmissionLeaseLostError(
                    "Stale owner cannot terminalize multipart initiation."
                )
            released = session.execute(
                text(
                    "UPDATE multipart_admission_leases "
                    "SET renewed_at = clock_timestamp(), expires_at = clock_timestamp() "
                    "WHERE upload_session_id = :session_id AND owner_id = :owner_id "
                    "AND epoch = :epoch RETURNING upload_session_id"
                ),
                {"session_id": session_id, "owner_id": owner_id, "epoch": epoch},
            ).scalar_one_or_none()
            if released is None:
                raise AdmissionLeaseLostError("Admission release lost its current fence.")

    def accept_completion(
        self,
        session_id: UUID,
        owner_id: UUID,
        epoch: int,
        request_id: UUID,
    ) -> AcceptedCompletion:
        """Durably accept completion, replaying idempotency before lease fencing."""
        request_sha256 = _multipart_request_sha256(
            "multipart-completion-accept", str(session_id), str(owner_id), str(epoch)
        )
        key = str(request_id)
        with self._sessions.begin() as session:
            replay = session.get(
                IdempotencyRecordModel,
                {"scope": "multipart-completion-accept", "key": key},
            )
            if replay is not None:
                return _replay_accepted_completion(replay, request_sha256, request_id)

            upload = session.get(UploadSessionModel, session_id, with_for_update=True)
            replay = session.get(
                IdempotencyRecordModel,
                {"scope": "multipart-completion-accept", "key": key},
            )
            if replay is not None:
                return _replay_accepted_completion(replay, request_sha256, request_id)
            if upload is None or upload.strategy != "MULTIPART":
                raise NotFoundError("Multipart session does not exist.")
            current_lease = session.scalar(
                select(MultipartAdmissionLeaseModel).where(
                    MultipartAdmissionLeaseModel.upload_session_id == session_id,
                    MultipartAdmissionLeaseModel.owner_id == owner_id,
                    MultipartAdmissionLeaseModel.epoch == epoch,
                    MultipartAdmissionLeaseModel.expires_at > func.clock_timestamp(),
                )
            )
            if current_lease is None:
                raise AdmissionLeaseLostError("Completion acceptance lost upload admission.")
            unresolved = session.scalar(
                select(func.count())
                .select_from(UploadPartModel)
                .where(
                    UploadPartModel.upload_session_id == session_id,
                    UploadPartModel.state != UploadPartState.VERIFIED.value,
                )
            )
            if upload.state != MultipartSessionState.IN_PROGRESS.value or unresolved:
                raise IllegalTransitionError("Completion requires every planned Part VERIFIED.")
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise ContentConflictError("Database time is unavailable.")
            upload.state = MultipartSessionState.COMPLETING.value
            upload.completion_requested_at = now
            upload.completion_reason = "PARTS_READY"
            upload.completion_phase = CompletionPhase.PENDING.value
            upload.last_activity_at = now
            current_lease.renewed_at = now
            current_lease.expires_at = now
            response = {"session_id": str(session_id), "state": "COMPLETING"}
            session.add(
                IdempotencyRecordModel(
                    scope="multipart-completion-accept",
                    key=key,
                    request_sha256=request_sha256,
                    resource_type="upload-session",
                    resource_id=session_id,
                    http_status=202,
                    response_json=response,
                )
            )
            session.flush()
            return AcceptedCompletion(
                request_id=request_id,
                session_id=MultipartSessionId(session_id),
            )

    def claim_completion(
        self, owner_instance_id: UUID, ttl_seconds: int, capacity: int
    ) -> CompletionClaim | None:
        """Claim one durable completion row with a separate server-side fence."""
        if ttl_seconds <= 0 or capacity <= 0:
            raise ContentConflictError("Completion TTL and capacity must be positive.")
        with self._sessions.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(782341902116)"))
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise ContentConflictError("Database time is unavailable.")
            active_count = session.scalar(
                select(func.count())
                .select_from(MultipartCompletionLeaseModel)
                .where(MultipartCompletionLeaseModel.expires_at > now)
            )
            if active_count is None or active_count >= capacity:
                return None
            upload = session.scalar(
                select(UploadSessionModel)
                .outerjoin(
                    MultipartCompletionLeaseModel,
                    MultipartCompletionLeaseModel.upload_session_id == UploadSessionModel.id,
                )
                .where(
                    UploadSessionModel.strategy == "MULTIPART",
                    UploadSessionModel.state == MultipartSessionState.COMPLETING.value,
                    or_(
                        MultipartCompletionLeaseModel.upload_session_id.is_(None),
                        MultipartCompletionLeaseModel.expires_at <= now,
                    ),
                )
                .order_by(UploadSessionModel.completion_requested_at, UploadSessionModel.id)
                .with_for_update(of=UploadSessionModel, skip_locked=True)
                .limit(1)
            )
            if upload is None:
                return None
            lease = session.get(MultipartCompletionLeaseModel, upload.id, with_for_update=True)
            if lease is None:
                lease = MultipartCompletionLeaseModel(
                    upload_session_id=upload.id,
                    owner_instance_id=owner_instance_id,
                    epoch=1,
                    acquired_at=now,
                    renewed_at=now,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                )
                session.add(lease)
            else:
                lease.owner_instance_id = owner_instance_id
                lease.epoch += 1
                lease.acquired_at = now
                lease.renewed_at = now
                lease.expires_at = now + timedelta(seconds=ttl_seconds)
            session.flush()
            if upload.completion_phase is None:
                raise ContentConflictError("Completion work has no durable phase.")
            lease_record = _completion_lease_record(lease)
            return CompletionClaim(
                session_id=MultipartSessionId(upload.id),
                lease=lease_record,
                phase=CompletionPhase(upload.completion_phase),
            )

    def renew_completion_lease(
        self,
        session_id: UUID,
        owner_instance_id: UUID,
        epoch: int,
        ttl_seconds: int,
    ) -> CompletionLeaseRecord:
        """Heartbeat only the current unexpired completion owner."""
        if ttl_seconds <= 0:
            raise ContentConflictError("Completion lease TTL must be positive.")
        with self._sessions.begin() as session:
            row = session.execute(
                text(
                    "UPDATE multipart_completion_leases SET renewed_at = clock_timestamp(), "
                    "expires_at = clock_timestamp() + make_interval(secs => :ttl_seconds) "
                    "WHERE upload_session_id = :session_id "
                    "AND owner_instance_id = :owner_id AND epoch = :epoch "
                    "AND expires_at > clock_timestamp() "
                    "RETURNING upload_session_id, owner_instance_id, epoch, acquired_at, "
                    "renewed_at, expires_at"
                ),
                {
                    "session_id": session_id,
                    "owner_id": owner_instance_id,
                    "epoch": epoch,
                    "ttl_seconds": ttl_seconds,
                },
            ).one_or_none()
            if row is None:
                raise AdmissionLeaseLostError("Completion lease is no longer current.")
            return CompletionLeaseRecord(
                session_id=MultipartSessionId(row.upload_session_id),
                lease_owner_id=CompletionLeaseOwnerId(row.owner_instance_id),
                lease_epoch=CompletionLeaseEpoch(row.epoch),
                expires_at=row.expires_at,
                last_heartbeat_at=row.renewed_at,
            )

    def set_completion_phase(self, claim: CompletionClaim, phase: CompletionPhase) -> None:
        """Advance durable completion work only under the current runner fence."""
        with self._sessions.begin() as session:
            changed = session.execute(
                text(
                    "UPDATE upload_sessions SET completion_phase = :phase, "
                    "last_activity_at = clock_timestamp() WHERE id = :session_id "
                    "AND state = 'COMPLETING' AND EXISTS (SELECT 1 "
                    "FROM multipart_completion_leases lease "
                    "WHERE lease.upload_session_id = upload_sessions.id "
                    "AND lease.owner_instance_id = :owner_id AND lease.epoch = :epoch "
                    "AND lease.expires_at > clock_timestamp()) RETURNING id"
                ),
                {
                    "phase": phase.value,
                    "session_id": claim.session_id.value,
                    "owner_id": claim.lease.lease_owner_id.value,
                    "epoch": claim.lease.lease_epoch.value,
                },
            ).scalar_one_or_none()
            if changed is None:
                raise AdmissionLeaseLostError("Stale completion owner cannot mutate work.")

    def recover_partial_completion(
        self, claim: CompletionClaim, evidence: PartialCompletionEvidence
    ) -> MultipartUploadContext:
        """Return one addressable partial MPU to upload ownership under a runner fence."""
        observations = {part.part_number: part for part in evidence.provider_parts}
        if len(observations) != len(evidence.provider_parts):
            raise ContentConflictError("Provider Part observations contain duplicate numbers.")
        with self._sessions.begin() as session:
            upload = session.get(UploadSessionModel, claim.session_id.value, with_for_update=True)
            lease = session.get(
                MultipartCompletionLeaseModel,
                claim.session_id.value,
                with_for_update=True,
            )
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise ContentConflictError("Database time is unavailable.")
            if (
                upload is None
                or upload.state != MultipartSessionState.COMPLETING.value
                or upload.provider_upload_id is None
                or lease is None
                or lease.owner_instance_id != claim.lease.lease_owner_id.value
                or lease.epoch != claim.lease.lease_epoch.value
                or lease.expires_at <= now
            ):
                raise AdmissionLeaseLostError(
                    "Stale completion owner cannot recover the provider attempt."
                )
            parts = session.scalars(
                select(UploadPartModel)
                .where(UploadPartModel.upload_session_id == upload.id)
                .order_by(UploadPartModel.part_number)
                .with_for_update()
            ).all()
            if set(observations) - {part.part_number for part in parts}:
                raise InvalidPartNumberError("Provider returned an unplanned Part.")

            upload.final_absence_observed_at = evidence.final_absence_observed_at
            upload.last_provider_reconciled_at = evidence.provider_reconciled_at
            upload.last_completion_result = evidence.completion_result
            session.flush()
            candidates: list[UploadPartModel] = []
            for part in parts:
                observation = observations.get(part.part_number)
                if _apply_provider_part_observation(
                    part, observation, evidence.provider_reconciled_at
                ):
                    if part.state == UploadPartState.PENDING.value:
                        part.state = UploadPartState.UPLOADED.value
                    candidates.append(part)
                else:
                    part.state = UploadPartState.PENDING.value
            session.flush()
            for part in candidates:
                part.state = UploadPartState.VERIFIED.value
                part.verified_at = evidence.provider_reconciled_at
            session.flush()

            upload.state = MultipartSessionState.IN_PROGRESS.value
            upload.completion_reason = None
            upload.completion_phase = None
            upload.last_activity_at = now
            lease.renewed_at = now
            lease.expires_at = now
            session.flush()
            return self._load_multipart_context(session, upload)

    def record_verified_completion(
        self, claim: CompletionClaim, evidence: VerificationEvidence
    ) -> MultipartUploadContext:
        """Commit structural evidence and publication under one completion fence."""
        with self._sessions.begin() as session:
            upload = session.get(UploadSessionModel, claim.session_id.value, with_for_update=True)
            lease = session.get(
                MultipartCompletionLeaseModel,
                claim.session_id.value,
                with_for_update=True,
            )
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise ContentConflictError("Database time is unavailable.")
            if (
                upload is None
                or upload.state != MultipartSessionState.COMPLETING.value
                or lease is None
                or lease.owner_instance_id != claim.lease.lease_owner_id.value
                or lease.epoch != claim.lease.lease_epoch.value
                or lease.expires_at <= now
            ):
                raise AdmissionLeaseLostError(
                    "Stale completion owner cannot publish verification evidence."
                )
            blob = session.get(BlobModel, upload.blob_id, with_for_update=True)
            if blob is None:
                raise ContentConflictError("Multipart Blob is unavailable.")
            evidence.validate_for(Sha256Digest.parse(blob.sha256), blob.size_bytes)
            if blob.state != BlobState.UPLOADING.value:
                raise IllegalTransitionError("Only an UPLOADING Blob can be published.")

            upload.completion_phase = CompletionPhase.FINAL_VERIFICATION.value
            upload.verification_method = evidence.verification_method.value
            upload.observed_sha256 = evidence.observed_sha256.value
            upload.observed_size_bytes = evidence.observed_size_bytes
            upload.verification_read_bytes = evidence.verification_read_bytes
            upload.verification_completed_at = evidence.verification_completed_at
            upload.verifier_implementation = evidence.verifier_version
            session.flush()

            blob.state = BlobState.VERIFYING.value
            session.flush()
            blob.state = BlobState.AVAILABLE.value
            blob.verified_at = evidence.verification_completed_at
            session.flush()

            upload.state = MultipartSessionState.COMPLETED.value
            upload.completed_at = evidence.verification_completed_at
            upload.last_activity_at = now
            lease.renewed_at = now
            lease.expires_at = now
            session.flush()
            return self._load_multipart_context(session, upload)

    def _load_upload_context(self, session: Session, upload: UploadSessionModel) -> UploadContext:
        blob = session.get(BlobModel, upload.blob_id)
        version = session.get(DatasetVersionModel, upload.initiating_version_id)
        if blob is None or version is None:
            raise ContentConflictError("UploadSession registry context is incomplete.")
        dataset = session.get(DatasetModel, version.dataset_id)
        if dataset is None:
            raise ContentConflictError("UploadSession Dataset is unavailable.")
        return _upload_context(version, dataset.name, blob, upload)

    def _replay_multipart_context(
        self,
        session: Session,
        replay: IdempotencyRecordModel,
        request_sha256: str,
    ) -> MultipartUploadContext:
        if replay.request_sha256 != request_sha256 or replay.resource_id is None:
            raise IdempotencyConflictError(
                "Multipart request ID was already used for a different request."
            )
        upload = session.get(UploadSessionModel, replay.resource_id)
        if upload is None or upload.strategy != "MULTIPART":
            raise IdempotencyConflictError("Multipart idempotency resource is unavailable.")
        return self._load_multipart_context(session, upload)

    def _load_multipart_context(
        self, session: Session, upload: UploadSessionModel
    ) -> MultipartUploadContext:
        blob = session.get(BlobModel, upload.blob_id)
        if blob is None or upload.session_generation is None:
            raise ContentConflictError("Multipart registry context is incomplete.")
        part_models = session.scalars(
            select(UploadPartModel)
            .where(UploadPartModel.upload_session_id == upload.id)
            .order_by(UploadPartModel.part_number)
        ).all()
        definitions = tuple(
            PartDefinition(
                part_number=part.part_number,
                offset_bytes=part.offset_bytes,
                size_bytes=part.size_bytes,
                expected_sha256=Sha256Digest.parse(part.sha256),
            )
            for part in part_models
        )
        if (
            upload.part_plan_schema_version is None
            or upload.part_size_bytes is None
            or upload.part_plan_sha256 is None
        ):
            raise ContentConflictError("Multipart session has no frozen PartPlan.")
        plan = PartPlan.build(
            schema_version=upload.part_plan_schema_version,
            blob_size_bytes=blob.size_bytes,
            part_size_bytes=upload.part_size_bytes,
            parts=definitions,
        )
        parts = tuple(_upload_part_record(model) for model in part_models)
        session_record = MultipartUploadSessionRecord(
            id=MultipartSessionId(upload.id),
            blob_id=upload.blob_id,
            initiating_version_id=upload.initiating_version_id,
            generation=MultipartSessionGeneration(upload.session_generation),
            state=MultipartSessionState(upload.state),
            part_plan=plan,
            provider_upload_id=upload.provider_upload_id,
            completion_phase=(
                CompletionPhase(upload.completion_phase)
                if upload.completion_phase is not None
                else None
            ),
            failure_code=upload.failure_code,
        )
        return MultipartUploadContext(
            session=session_record,
            blob=_blob_record(blob),
            parts=parts,
        )

    @staticmethod
    def _get_or_create_blob(session: Session, digest: Sha256Digest, size_bytes: int) -> BlobModel:
        blob_id = session.scalar(
            insert(BlobModel)
            .values(
                sha256=digest.value,
                size_bytes=size_bytes,
                object_key=object_key_for(digest),
                state="PENDING",
            )
            .on_conflict_do_nothing(index_elements=[BlobModel.sha256])
            .returning(BlobModel.id)
        )
        blob = (
            session.get(BlobModel, blob_id)
            if blob_id is not None
            else session.scalar(select(BlobModel).where(BlobModel.sha256 == digest.value))
        )
        if blob is None:
            raise ContentConflictError("Blob identity could not be resolved.")
        if blob.size_bytes != size_bytes:
            raise ContentConflictError("One SHA-256 digest has conflicting sizes.")
        return blob


def _dataset_record(model: DatasetModel) -> DatasetRecord:
    return DatasetRecord(id=model.id, name=DatasetName.parse(model.name))


def _version_record(model: DatasetVersionModel, dataset_name: str) -> VersionRecord:
    return VersionRecord(
        id=model.id,
        dataset_id=model.dataset_id,
        dataset_name=DatasetName.parse(dataset_name),
        version_number=model.version_number,
        manifest_sha256=Sha256Digest.parse(model.manifest_sha256),
        state=VersionState(model.state),
        file_count=model.file_count,
        logical_bytes=model.logical_bytes,
        unique_blob_count=model.unique_blob_count,
        unique_blob_bytes=model.unique_blob_bytes,
        failure_code=FailureCode(model.failure_code) if model.failure_code is not None else None,
        failure_detail=model.failure_detail,
    )


def _blob_record(model: BlobModel) -> BlobRecord:
    return BlobRecord(
        id=model.id,
        sha256=Sha256Digest.parse(model.sha256),
        size_bytes=model.size_bytes,
        object_key=model.object_key,
        state=BlobState(model.state),
        failure_code=FailureCode(model.failure_code) if model.failure_code is not None else None,
    )


def _upload_session_record(model: UploadSessionModel) -> UploadSessionRecord:
    return UploadSessionRecord(
        id=model.id,
        blob_id=model.blob_id,
        initiating_version_id=model.initiating_version_id,
        state=UploadSessionState(model.state),
        etag=model.etag,
    )


def _upload_context(
    version: DatasetVersionModel,
    dataset_name: str,
    blob: BlobModel,
    upload: UploadSessionModel | None,
) -> UploadContext:
    return UploadContext(
        version=_version_record(version, dataset_name),
        blob=_blob_record(blob),
        session=_upload_session_record(upload) if upload is not None else None,
    )


def _multipart_request_sha256(scope: str, *values: str) -> str:
    payload = json.dumps(
        {"scope": scope, "values": list(values)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _admission_lease_record(model: MultipartAdmissionLeaseModel) -> AdmissionLeaseRecord:
    return AdmissionLeaseRecord(
        session_id=MultipartSessionId(model.upload_session_id),
        invocation_id=MultipartInvocationId(model.invocation_id),
        lease_owner_id=AdmissionLeaseOwnerId(model.owner_id),
        lease_epoch=AdmissionLeaseEpoch(model.epoch),
        expires_at=model.expires_at,
        last_renewed_at=model.renewed_at,
    )


def _replay_admission_lease(
    replay: IdempotencyRecordModel, request_sha256: str
) -> AdmissionLeaseRecord:
    if replay.request_sha256 != request_sha256 or replay.response_json is None:
        raise IdempotencyConflictError(
            "Admission request ID was already used for a different request."
        )
    response = replay.response_json
    try:
        return AdmissionLeaseRecord(
            session_id=MultipartSessionId(UUID(str(response["session_id"]))),
            invocation_id=MultipartInvocationId(UUID(str(response["invocation_id"]))),
            lease_owner_id=AdmissionLeaseOwnerId(UUID(str(response["owner_id"]))),
            lease_epoch=AdmissionLeaseEpoch(int(str(response["epoch"]))),
            expires_at=datetime.fromisoformat(str(response["expires_at"])),
            last_renewed_at=datetime.fromisoformat(str(response["renewed_at"])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise IdempotencyConflictError("Admission replay response is invalid.") from error


def _completion_lease_record(model: MultipartCompletionLeaseModel) -> CompletionLeaseRecord:
    return CompletionLeaseRecord(
        session_id=MultipartSessionId(model.upload_session_id),
        lease_owner_id=CompletionLeaseOwnerId(model.owner_instance_id),
        lease_epoch=CompletionLeaseEpoch(model.epoch),
        expires_at=model.expires_at,
        last_heartbeat_at=model.renewed_at,
    )


def _replay_accepted_completion(
    replay: IdempotencyRecordModel,
    request_sha256: str,
    request_id: UUID,
) -> AcceptedCompletion:
    if replay.request_sha256 != request_sha256 or replay.resource_id is None:
        raise IdempotencyConflictError(
            "Completion request ID was already used for a different request."
        )
    if replay.http_status != 202:
        raise IdempotencyConflictError("Completion replay does not contain the accepted response.")
    return AcceptedCompletion(
        request_id=request_id,
        session_id=MultipartSessionId(replay.resource_id),
    )


def _upload_part_record(model: UploadPartModel) -> UploadPartRecord:
    receipt = None
    if (
        model.upload_response_etag is not None
        and model.upload_response_checksum_sha256_base64 is not None
    ):
        receipt = CompletedPartReceipt.from_upload_response(
            part_number=model.part_number,
            response_etag=model.upload_response_etag,
            response_checksum_sha256_base64=model.upload_response_checksum_sha256_base64,
            expected_sha256=Sha256Digest.parse(model.sha256),
        )
    return UploadPartRecord(
        session_id=MultipartSessionId(model.upload_session_id),
        definition=PartDefinition(
            part_number=model.part_number,
            offset_bytes=model.offset_bytes,
            size_bytes=model.size_bytes,
            expected_sha256=Sha256Digest.parse(model.sha256),
        ),
        state=UploadPartState(model.state),
        response_receipt=receipt,
    )


def _apply_provider_part_observation(
    part: UploadPartModel,
    observation: ProviderPartObservation | None,
    observed_at: datetime,
) -> bool:
    """Persist only structurally safe provider evidence and diagnose mismatches."""
    if observation is None:
        part.listed_etag = None
        part.listed_checksum_sha256_base64 = None
        part.listed_size_bytes = None
        part.provider_listed_at = None
        part.verified_at = None
        part.last_error_code = "PART_NOT_PRESENT"
        return False

    expected_checksum = Sha256Digest.parse(part.sha256).checksum_base64
    size_matches = observation.size_bytes == part.size_bytes
    checksum_matches = observation.checksum_sha256_base64 == expected_checksum
    part.listed_etag = observation.etag
    part.listed_checksum_sha256_base64 = (
        observation.checksum_sha256_base64 if checksum_matches else None
    )
    part.listed_size_bytes = observation.size_bytes
    part.provider_listed_at = observed_at

    if not size_matches:
        part.verified_at = None
        part.last_error_code = PartSizeMismatchError.code
        return False
    if not checksum_matches:
        part.verified_at = None
        part.last_error_code = PartChecksumRejectedError.code
        return False
    if part.upload_response_etag is None or part.upload_response_checksum_sha256_base64 is None:
        part.verified_at = None
        part.last_error_code = "PART_RECEIPT_MISSING"
        return False
    if (
        part.upload_response_etag != observation.etag
        or part.upload_response_checksum_sha256_base64 != observation.checksum_sha256_base64
    ):
        part.verified_at = None
        part.last_error_code = "PART_RECEIPT_MISMATCH"
        return False
    part.last_error_code = None
    return True
