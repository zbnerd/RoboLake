import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_service_info(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == {"name": "RoboLake", "status": "running"}


async def test_health_check(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
