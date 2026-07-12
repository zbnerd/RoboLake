"""PostgreSQL implementation of the RoboLake registry port."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from robolake.application.contracts import UploadContext
from robolake.domain.errors import (
    ContentConflictError,
    IdempotencyConflictError,
    IllegalTransitionError,
    ManifestMismatchError,
    NotFoundError,
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
from robolake.domain.records import (
    BlobRecord,
    ContentStatus,
    DatasetRecord,
    SnapshotStatus,
    UploadSessionRecord,
    VersionRecord,
    VersionStatus,
)
from robolake.infrastructure.models import (
    BlobModel,
    DatasetEntryModel,
    DatasetModel,
    DatasetVersionModel,
    IdempotencyRecordModel,
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

    def _load_upload_context(self, session: Session, upload: UploadSessionModel) -> UploadContext:
        blob = session.get(BlobModel, upload.blob_id)
        version = session.get(DatasetVersionModel, upload.initiating_version_id)
        if blob is None or version is None:
            raise ContentConflictError("UploadSession registry context is incomplete.")
        dataset = session.get(DatasetModel, version.dataset_id)
        if dataset is None:
            raise ContentConflictError("UploadSession Dataset is unavailable.")
        return _upload_context(version, dataset.name, blob, upload)

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
