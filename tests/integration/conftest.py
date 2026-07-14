"""Isolated PostgreSQL fixtures for M1 integration behavior."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from robolake.infrastructure.settings import Settings
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def temporary_database_url() -> Iterator[str]:
    """Create a disposable PostgreSQL database without touching the development database."""
    configured_url = make_url(Settings().database_url)
    maintenance_url = configured_url.set(database="postgres")
    database_name = f"robolake_test_{uuid4().hex}"
    admin_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    try:
        yield configured_url.set(database=database_name).render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


@pytest.fixture(scope="session")
def migrated_database_url(temporary_database_url: str, repository_root: Path) -> Iterator[str]:
    environment = {**os.environ, "ROBOLAKE_DATABASE_URL": temporary_database_url}
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    yield temporary_database_url


@pytest.fixture
def isolated_migrated_database_url(repository_root: Path) -> Iterator[str]:
    """Create one migration-complete database for tests that commit independent UoWs."""
    configured_url = make_url(Settings().database_url)
    maintenance_url = configured_url.set(database="postgres")
    database_name = f"robolake_isolated_{uuid4().hex}"
    admin_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    database_url = configured_url.set(database=database_name).render_as_string(hide_password=False)
    environment = {**os.environ, "ROBOLAKE_DATABASE_URL": database_url}
    try:
        subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        yield database_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


@pytest.fixture
def database_connection(migrated_database_url: str) -> Iterator[Connection]:
    engine = create_engine(migrated_database_url)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
