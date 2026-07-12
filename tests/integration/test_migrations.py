"""Integration behavior for the M1 Alembic migration."""

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.integration


def test_m1_migration_creates_registry_schema_at_head(migrated_database_url: str) -> None:
    engine = create_engine(migrated_database_url)
    try:
        inspector = inspect(engine)
        assert {
            "datasets",
            "dataset_versions",
            "blobs",
            "dataset_entries",
            "upload_sessions",
            "idempotency_records",
        }.issubset(inspector.get_table_names())
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("20260711_0002")
    finally:
        engine.dispose()


def test_m1_migration_downgrades_cleanly_and_reapplies(
    migrated_database_url: str, repository_root: Path
) -> None:
    environment = {**os.environ, "ROBOLAKE_DATABASE_URL": migrated_database_url}
    command = ["uv", "run", "alembic"]
    subprocess.run(
        [*command, "downgrade", "20260710_0001"],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        engine = create_engine(migrated_database_url)
        try:
            assert not {
                "datasets",
                "dataset_versions",
                "blobs",
                "dataset_entries",
                "upload_sessions",
                "idempotency_records",
            }.intersection(inspect(engine).get_table_names())
        finally:
            engine.dispose()
    finally:
        subprocess.run(
            [*command, "upgrade", "head"],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )


def test_sqlalchemy_metadata_matches_migration_head(
    migrated_database_url: str, repository_root: Path
) -> None:
    environment = {**os.environ, "ROBOLAKE_DATABASE_URL": migrated_database_url}

    result = subprocess.run(
        ["uv", "run", "alembic", "check"],
        cwd=repository_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
