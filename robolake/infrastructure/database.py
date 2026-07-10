"""SQLAlchemy configuration and connectivity probe."""

from dataclasses import dataclass, field

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from robolake.infrastructure.settings import Settings


class Base(DeclarativeBase):
    """Base class for future SQLAlchemy mappings."""


def create_database_engine(settings: Settings) -> Engine:
    """Create the shared SQLAlchemy engine without opening a connection."""
    return create_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create the shared unit-of-work session factory."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@dataclass(frozen=True)
class DatabaseHealthProbe:
    """Verify PostgreSQL connectivity with the smallest possible query."""

    engine: Engine
    name: str = field(init=False, default="database")

    def check(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
