"""PostgreSQL repository contract for M2 session registration."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from robolake.domain.errors import IdempotencyConflictError
from robolake.domain.records import HashedMultipartPlan, MultipartUploadContext
from robolake.infrastructure.database import create_session_factory
from robolake.infrastructure.store import SqlAlchemyStore
from sqlalchemy import Engine, create_engine, text
from tests.integration.m2_helpers import build_multipart_plan, insert_large_registry_context

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


def test_create_or_resolve_multipart_persists_frozen_plan_and_replays(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    plan = HashedMultipartPlan.from_plan(build_multipart_plan())
    with engine.begin() as connection:
        _, version_id, blob_id = insert_large_registry_context(connection)
    request_id = uuid4()

    created = store.create_or_resolve_multipart(request_id, version_id, blob_id, plan)
    replayed = store.create_or_resolve_multipart(request_id, version_id, blob_id, plan)

    assert replayed == created
    assert created.session.generation.value == 1
    assert created.session.part_plan == plan.plan
    assert len(created.parts) == plan.plan.part_count


def test_active_session_converges_and_terminal_generation_is_not_reactivated(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    plan = HashedMultipartPlan.from_plan(build_multipart_plan())
    with engine.begin() as connection:
        _, version_id, blob_id = insert_large_registry_context(connection)

    first = store.create_or_resolve_multipart(uuid4(), version_id, blob_id, plan)
    converged = store.create_or_resolve_multipart(uuid4(), version_id, blob_id, plan)
    assert converged.session.id == first.session.id

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE upload_sessions SET state = 'CANCELLED' WHERE id = :id"),
            {"id": first.session.id.value},
        )
    second = store.create_or_resolve_multipart(uuid4(), version_id, blob_id, plan)

    assert second.session.id != first.session.id
    assert second.session.generation.value == 2


def test_request_id_cannot_be_retargeted(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    plan = HashedMultipartPlan.from_plan(build_multipart_plan())
    with engine.begin() as connection:
        _, first_version, first_blob = insert_large_registry_context(connection)
        _, second_version, second_blob = insert_large_registry_context(connection)
    request_id = uuid4()
    store.create_or_resolve_multipart(request_id, first_version, first_blob, plan)

    with pytest.raises(IdempotencyConflictError):
        store.create_or_resolve_multipart(request_id, second_version, second_blob, plan)


def test_concurrent_creators_converge_on_one_active_generation(
    store_and_engine: tuple[SqlAlchemyStore, Engine],
) -> None:
    store, engine = store_and_engine
    plan = HashedMultipartPlan.from_plan(build_multipart_plan())
    with engine.begin() as connection:
        _, version_id, blob_id = insert_large_registry_context(connection)
    barrier = Barrier(2)

    def create() -> MultipartUploadContext:
        barrier.wait()
        return store.create_or_resolve_multipart(uuid4(), version_id, blob_id, plan)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(create)
        second_future = executor.submit(create)
        first = first_future.result()
        second = second_future.result()

    assert first.session.id == second.session.id
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM upload_sessions WHERE blob_id = :blob_id "
                    "AND state IN ('CREATED','INITIATING','IN_PROGRESS','COMPLETING','ABORTING')"
                ),
                {"blob_id": blob_id},
            ).scalar_one()
            == 1
        )
