from fastapi import FastAPI

from robolake.api.router import api_router


def create_app() -> FastAPI:
    application = FastAPI(
        title="RoboLake",
        summary="Data Lake for Physical AI",
        version="0.1.0",
    )
    application.include_router(api_router)
    return application


app = create_app()
