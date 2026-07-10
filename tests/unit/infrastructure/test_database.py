from types import TracebackType
from typing import Any, cast

import pytest
from robolake.infrastructure.database import DatabaseHealthProbe
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.unit


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))


class FakeEngine:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    def connect(self) -> FakeConnection:
        return self.connection


def test_database_probe_executes_select_one() -> None:
    engine = FakeEngine()

    DatabaseHealthProbe(cast(Engine, engine)).check()

    assert engine.connection.statements == ["SELECT 1"]
