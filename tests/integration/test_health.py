import pytest
from apps.api.main import create_app
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_health_reports_real_postgresql_and_minio_connectivity() -> None:
    app = create_app()

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
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
