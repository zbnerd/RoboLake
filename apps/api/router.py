"""Top-level API router."""

from fastapi import APIRouter

from apps.api.routes import datasets, system, transfers, versions

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(datasets.router, prefix="/v1")
api_router.include_router(versions.router, prefix="/v1")
api_router.include_router(transfers.router, prefix="/v1")
