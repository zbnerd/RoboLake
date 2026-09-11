"""FastAPI application composition root."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from robolake import __version__
from robolake.application.health import HealthService
from robolake.application.registry import RegistryService
from robolake.application.transfers import TransferService
from robolake.domain.constants import MAX_MANIFEST_BYTES
from robolake.infrastructure.database import (
    DatabaseHealthProbe,
    create_database_engine,
    create_session_factory,
)
from robolake.infrastructure.object_storage import (
    ObjectStorageHealthProbe,
    S3ObjectStore,
    create_public_s3_client,
    create_s3_client,
)
from robolake.infrastructure.settings import Settings
from robolake.infrastructure.store import SqlAlchemyStore

from apps.api.dependencies import ApiServices
from apps.api.errors import install_exception_handlers
from apps.api.middleware.body_limit import BodyLimitMiddleware
from apps.api.router import api_router


def build_api_services(settings: Settings) -> ApiServices:
    """Compose one owned modular-monolith control plane."""
    engine = create_database_engine(settings)
    internal_client = create_s3_client(settings)
    public_client = create_public_s3_client(settings)
    store = SqlAlchemyStore(create_session_factory(engine))

    def close() -> None:
        public_client.close()
        internal_client.close()
        engine.dispose()

    return ApiServices(
        health=HealthService(
            [
                DatabaseHealthProbe(engine),
                ObjectStorageHealthProbe(internal_client, settings.s3_bucket),
            ]
        ),
        registry=RegistryService(store),
        transfers=TransferService(
            store,
            S3ObjectStore(internal_client, public_client, settings.s3_bucket),
            presigned_url_ttl_seconds=settings.presigned_url_ttl_seconds,
            stream_chunk_bytes=settings.stream_chunk_bytes,
        ),
        close=close,
    )


def create_app(services: ApiServices | None = None) -> FastAPI:
    """Create the API with injectable services for isolated tests."""
    if services is None:
        services = build_api_services(Settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        services.close()

    application = FastAPI(
        title="RoboLake",
        summary="Robot dataset transfer and registry platform",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.services = services
    application.state.health_service = services.health
    install_exception_handlers(application)
    application.add_middleware(BodyLimitMiddleware, max_body_bytes=MAX_MANIFEST_BYTES)
    application.include_router(api_router)
    return application


app = create_app()
