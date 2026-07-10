"""Top-level API router."""

from fastapi import APIRouter

from apps.api.routes import system

api_router = APIRouter()
api_router.include_router(system.router)
