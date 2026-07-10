"""FastAPI application composition root."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from robolake import __version__
from robolake.application.health import HealthService
from robolake.infrastructure.database import DatabaseHealthProbe, create_database_engine
from robolake.infrastructure.object_storage import (
    ObjectStorageHealthProbe,
    create_s3_client,
)
from robolake.infrastructure.settings import Settings
from sqlalchemy import Engine

from apps.api.router import api_router


def create_app(health_service: HealthService | None = None) -> FastAPI:
    """Create the API with injectable health dependencies for isolated tests."""
    owned_engine: Engine | None = None
    owned_s3_client = None

    if health_service is None:
        settings = Settings()
        owned_engine = create_database_engine(settings)
        owned_s3_client = create_s3_client(settings)
        health_service = HealthService(
            [
                DatabaseHealthProbe(owned_engine),
                ObjectStorageHealthProbe(owned_s3_client, settings.s3_bucket),
            ]
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owned_s3_client is not None:
            owned_s3_client.close()
        if owned_engine is not None:
            owned_engine.dispose()

    application = FastAPI(
        title="RoboLake",
        summary="Robot dataset transfer and registry platform",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.health_service = health_service
    application.include_router(api_router)
    return application


app = create_app()
