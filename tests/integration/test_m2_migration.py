"""Upgrade and rollback contract for the M2 PostgreSQL schema."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from robolake.infrastructure.settings import Settings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from tests.integration.m2_helpers import insert_multipart_session

pytestmark = pytest.mark.integration


@pytest.fixture
def migration_database_url() -> Iterator[str]:
    configured = make_url(Settings().database_url)
    maintenance = create_engine(configured.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = f"robolake_m2_migration_{uuid4().hex}"
    with maintenance.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield configured.set(database=name).render_as_string(hide_password=False)
    finally:
        with maintenance.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        maintenance.dispose()


def _alembic(
    database_url: str, repository_root: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "alembic", *arguments],
        cwd=repository_root,
        env={**os.environ, "ROBOLAKE_DATABASE_URL": database_url},
        check=False,
        capture_output=True,
        text=True,
    )


def _insert_m1_row(database_url: str) -> tuple[str, str]:
    engine = create_engine(database_url)
    dataset_id = uuid4()
    version_id = uuid4()
    blob_id = uuid4()
    session_id = uuid4()
    now = datetime.now(UTC)
    digest = uuid4().hex * 2
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO datasets (id, name, created_at) VALUES (:id, :name, :now)"),
                {"id": dataset_id, "name": f"migration/m1-{dataset_id}", "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO blobs (id, sha256, size_bytes, object_key, state, created_at) "
                    "VALUES (:id, :sha, 0, :key, 'PENDING', :now)"
                ),
                {
                    "id": blob_id,
                    "sha": digest,
                    "key": f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}",
                    "now": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions "
                    "(id, dataset_id, version_number, manifest_schema_version, manifest_bytes, "
                    "manifest_sha256, state, file_count, logical_bytes, unique_blob_count, "
                    "unique_blob_bytes, created_at, sealed_at) "
                    "VALUES (:id, :dataset_id, 1, 1, :manifest, :sha, 'DRAFT', 1, 0, 1, 0, "
                    ":now, NULL)"
                ),
                {
                    "id": version_id,
                    "dataset_id": dataset_id,
                    "manifest": b'{"schema_version":1,"entries":[]}',
                    "sha": uuid4().hex * 2,
                    "now": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_entries "
                    "(dataset_version_id, manifest_ordinal, relative_path, blob_id) "
                    "VALUES (:version_id, 0, 'empty.bin', :blob_id)"
                ),
                {"version_id": version_id, "blob_id": blob_id},
            )
            connection.execute(
                text("UPDATE dataset_versions SET sealed_at = :now WHERE id = :id"),
                {"id": version_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO upload_sessions "
                    "(id, blob_id, initiating_version_id, strategy, state, created_at, "
                    "last_activity_at) VALUES (:id, :blob_id, :version_id, 'SINGLE_PUT', "
                    "'CREATED', :now, :now)"
                ),
                {"id": session_id, "blob_id": blob_id, "version_id": version_id, "now": now},
            )
            connection.execute(
                text("UPDATE upload_sessions SET state = 'IN_PROGRESS' WHERE id = :id"),
                {"id": session_id},
            )
            connection.execute(
                text("UPDATE blobs SET state = 'UPLOADING' WHERE id = :id"),
                {"id": blob_id},
            )
            connection.execute(
                text("UPDATE blobs SET state = 'VERIFYING' WHERE id = :id"),
                {"id": blob_id},
            )
            connection.execute(
                text("UPDATE blobs SET state = 'AVAILABLE', verified_at = :now WHERE id = :id"),
                {"id": blob_id, "now": now},
            )
            connection.execute(
                text(
                    "UPDATE upload_sessions SET state = 'COMPLETED', completed_at = :now "
                    "WHERE id = :id"
                ),
                {"id": session_id, "now": now},
            )
            connection.execute(
                text(
                    "UPDATE dataset_versions SET state = 'VERIFYING', verifying_at = :now "
                    "WHERE id = :id"
                ),
                {"id": version_id, "now": now},
            )
            connection.execute(
                text("UPDATE dataset_versions SET state = 'READY', ready_at = :now WHERE id = :id"),
                {"id": version_id, "now": now},
            )
    finally:
        engine.dispose()
    return str(session_id), digest


def test_upgrade_from_v01_preserves_m1_rows_and_adds_m2_schema(
    migration_database_url: str, repository_root: Path
) -> None:
    assert (
        _alembic(migration_database_url, repository_root, "upgrade", "20260711_0002").returncode
        == 0
    )
    session_id, digest = _insert_m1_row(migration_database_url)

    result = _alembic(migration_database_url, repository_root, "upgrade", "head")

    assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(migration_database_url)
    try:
        inspector = inspect(engine)
        assert {
            "upload_parts",
            "multipart_admission_leases",
            "multipart_completion_leases",
        }.issubset(inspector.get_table_names())
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("20260714_0003")
            row = connection.execute(
                text(
                    "SELECT strategy, state, session_generation, part_plan_sha256 "
                    "FROM upload_sessions WHERE id = :id"
                ),
                {"id": session_id},
            ).one()
            assert row == ("SINGLE_PUT", "COMPLETED", None, None)
            assert connection.execute(
                text("SELECT sha256, state FROM blobs WHERE sha256 = :sha"), {"sha": digest}
            ).one() == (digest, "AVAILABLE")
            assert (
                connection.execute(
                    text(
                        "SELECT state FROM dataset_versions WHERE id = "
                        "(SELECT initiating_version_id FROM upload_sessions WHERE id = :id)"
                    ),
                    {"id": session_id},
                ).scalar_one()
                == "READY"
            )
    finally:
        engine.dispose()


def test_permitted_downgrade_preserves_m1_data_and_reupgrades(
    migration_database_url: str, repository_root: Path
) -> None:
    assert (
        _alembic(migration_database_url, repository_root, "upgrade", "20260711_0002").returncode
        == 0
    )
    session_id, _ = _insert_m1_row(migration_database_url)
    assert _alembic(migration_database_url, repository_root, "upgrade", "head").returncode == 0

    downgrade = _alembic(migration_database_url, repository_root, "downgrade", "20260711_0002")

    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    engine = create_engine(migration_database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT strategy FROM upload_sessions WHERE id = :id"), {"id": session_id}
                ).scalar_one()
                == "SINGLE_PUT"
            )
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("20260711_0002")
    finally:
        engine.dispose()

    reupgrade = _alembic(migration_database_url, repository_root, "upgrade", "head")
    assert reupgrade.returncode == 0, reupgrade.stdout + reupgrade.stderr


def test_downgrade_refuses_any_multipart_workflow_row(
    migration_database_url: str, repository_root: Path
) -> None:
    assert _alembic(migration_database_url, repository_root, "upgrade", "head").returncode == 0
    engine = create_engine(migration_database_url)
    try:
        with engine.begin() as connection:
            insert_multipart_session(connection)
        result = _alembic(migration_database_url, repository_root, "downgrade", "20260711_0002")
        assert result.returncode != 0
        assert "multipart workflow rows must be archived" in result.stdout + result.stderr
    finally:
        engine.dispose()
