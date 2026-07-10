from fastapi import APIRouter

from robolake.api.routes import system

api_router = APIRouter()
api_router.include_router(system.router)
