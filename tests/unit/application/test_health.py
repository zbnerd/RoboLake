from dataclasses import dataclass

import pytest
from robolake.application.health import HealthService

pytestmark = pytest.mark.unit


@dataclass
class PassingProbe:
    name: str
    calls: int = 0

    def check(self) -> None:
        self.calls += 1


@dataclass
class FailingProbe:
    name: str
    calls: int = 0

    def check(self) -> None:
        self.calls += 1
        raise RuntimeError("sensitive dependency detail")


def test_health_is_ok_when_every_probe_passes() -> None:
    database = PassingProbe("database")
    object_storage = PassingProbe("object_storage")

    report = HealthService([database, object_storage]).check()

    assert report.status == "ok"
    assert report.components == {
        "application": "ok",
        "database": "ok",
        "object_storage": "ok",
    }
    assert database.calls == 1
    assert object_storage.calls == 1


def test_health_is_degraded_and_checks_every_probe_after_failure() -> None:
    database = FailingProbe("database")
    object_storage = PassingProbe("object_storage")

    report = HealthService([database, object_storage]).check()

    assert report.status == "degraded"
    assert report.components == {
        "application": "ok",
        "database": "down",
        "object_storage": "ok",
    }
    assert database.calls == 1
    assert object_storage.calls == 1
    assert "sensitive dependency detail" not in repr(report)
