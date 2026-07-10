from dataclasses import dataclass

import pytest
from apps.api.main import create_app
from httpx import ASGITransport, AsyncClient
from robolake.application.health import HealthService

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@dataclass
class PassingProbe:
    name: str

    def check(self) -> None:
        return None


@dataclass
class FailingProbe:
    name: str

    def check(self) -> None:
        raise RuntimeError("provider connection string must stay private")


async def test_service_info_identifies_robolake() -> None:
    app = create_app(HealthService([]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "RoboLake",
        "status": "running",
        "version": "0.1.0",
    }


async def test_health_returns_ok_when_dependencies_are_reachable() -> None:
    service = HealthService([PassingProbe("database"), PassingProbe("object_storage")])
    app = create_app(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "components": {
            "application": "ok",
            "database": "ok",
            "object_storage": "ok",
        },
    }


async def test_health_returns_503_without_exposing_dependency_error() -> None:
    service = HealthService([FailingProbe("database"), PassingProbe("object_storage")])
    app = create_app(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "components": {
            "application": "ok",
            "database": "down",
            "object_storage": "ok",
        },
    }
    assert "provider connection string" not in response.text
