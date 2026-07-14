"""M1 single-PUT operations must never consume an M2 multipart session."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from robolake.application.contracts import ObjectInfo, PresignedRequest
from robolake.application.transfers import TransferService
from robolake.domain.errors import IllegalTransitionError
from robolake.domain.identifiers import Sha256Digest
from robolake.domain.lifecycle import FailureCode
from robolake.infrastructure.database import create_session_factory
from robolake.infrastructure.store import SqlAlchemyStore
from sqlalchemy import Engine, create_engine, text
from tests.integration.m2_helpers import (
    MultipartDatabaseContext,
    insert_multipart_session,
    move_session_to_in_progress,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def store_and_engine(
    isolated_migrated_database_url: str,
) -> Iterator[tuple[SqlAlchemyStore, Engine]]:
    engine = create_engine(isolated_migrated_database_url)
    try:
        yield SqlAlchemyStore(create_session_factory(engine)), engine
    finally:
        engine.dispose()


@dataclass
class CountingObjectStore:
    calls: list[str] = field(default_factory=list)

    def head(self, object_key: str) -> ObjectInfo | None:
        self.calls.append("head")
        return None

    def iter_bytes(self, object_key: str, chunk_size: int) -> Iterator[bytes]:
        self.calls.append("iter_bytes")
        return iter(())

    def presign_put(
        self,
        object_key: str,
        size_bytes: int,
        checksum_base64: str,
        expires_seconds: int,
    ) -> PresignedRequest:
        self.calls.append("presign_put")
        return PresignedRequest("https://storage.invalid/private-capability", {})

    def presign_get(self, object_key: str, expires_seconds: int) -> PresignedRequest:
        self.calls.append("presign_get")
        return PresignedRequest("https://storage.invalid/private-capability", {})


def _created_session(engine: Engine) -> MultipartDatabaseContext:
    with engine.begin() as connection:
        return insert_multipart_session(connection)


def _in_progress_session(engine: Engine) -> MultipartDatabaseContext:
    with engine.begin() as connection:
        context = insert_multipart_session(connection)
        move_session_to_in_progress(connection, context)
        return context


def test_m1_prepare_rejects_active_multipart_before_object_storage(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _created_session(engine)
    objects = CountingObjectStore()
    service = TransferService(store, objects)
    with engine.connect() as connection:
        digest = Sha256Digest.parse(
            connection.execute(
                text("SELECT sha256 FROM blobs WHERE id = :id"),
                {"id": context.blob_id},
            ).scalar_one()
        )

    with pytest.raises(IllegalTransitionError):
        service.prepare_upload(context.version_id, digest, str(uuid4()))

    assert objects.calls == []


@pytest.mark.parametrize("operation", ["url", "status", "complete"])
def test_m1_session_operations_reject_multipart_before_object_storage(
    store_and_engine: tuple[SqlAlchemyStore, Engine], operation: str
) -> None:
    store, engine = store_and_engine
    context = _in_progress_session(engine)
    objects = CountingObjectStore()
    service = TransferService(store, objects)

    with pytest.raises(IllegalTransitionError):
        if operation == "url":
            service.issue_upload_url(context.session_id)
        elif operation == "status":
            service.upload_status(context.session_id)
        else:
            service.complete_upload(context.session_id)

    assert objects.calls == []


@pytest.mark.parametrize("operation", ["get", "start", "verify", "fail"])
def test_every_m1_repository_mutation_rejects_multipart(
    store_and_engine: tuple[SqlAlchemyStore, Engine], operation: str
) -> None:
    store, engine = store_and_engine
    context = (
        _in_progress_session(engine)
        if operation in {"verify", "fail"}
        else _created_session(engine)
    )

    with pytest.raises(IllegalTransitionError):
        if operation == "get":
            store.get_upload(context.session_id)
        elif operation == "start":
            store.mark_upload_in_progress(context.session_id)
        elif operation == "verify":
            store.mark_upload_verified(context.session_id, '"etag"')
        else:
            store.mark_upload_failed(
                context.session_id,
                FailureCode.STORED_OBJECT_MISMATCH,
                "synthetic mismatch",
            )


def test_m1_restart_does_not_replace_a_failed_multipart_generation(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    context = _created_session(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE upload_sessions SET state = 'FAILED', failure_code = 'PLAN_INVARIANT' "
                "WHERE id = :session_id"
            ),
            {"session_id": context.session_id},
        )
        connection.execute(
            text("UPDATE blobs SET state = 'UPLOADING' WHERE id = :blob_id"),
            {"blob_id": context.blob_id},
        )
        connection.execute(
            text("UPDATE blobs SET state = 'VERIFYING' WHERE id = :blob_id"),
            {"blob_id": context.blob_id},
        )
        connection.execute(
            text("UPDATE blobs SET state = 'FAILED' WHERE id = :blob_id"),
            {"blob_id": context.blob_id},
        )
        connection.execute(
            text("UPDATE dataset_versions SET state = 'UPLOADING' WHERE id = :version_id"),
            {"version_id": context.version_id},
        )
        connection.execute(
            text("UPDATE dataset_versions SET state = 'VERIFYING' WHERE id = :version_id"),
            {"version_id": context.version_id},
        )
        connection.execute(
            text("UPDATE dataset_versions SET state = 'FAILED' WHERE id = :version_id"),
            {"version_id": context.version_id},
        )

    with pytest.raises(IllegalTransitionError):
        store.restart_failed_upload(
            context.version_id,
            context.blob_id,
            str(uuid4()),
            Sha256Digest("ab" * 32),
        )
