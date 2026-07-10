"""Service identity and dependency health routes."""

from typing import cast

from fastapi import APIRouter, Request, Response, status
from robolake import __version__
from robolake.application.health import HealthService

from apps.api.schemas.system import HealthResponse, ServiceInfo

router = APIRouter(tags=["system"])


@router.get("/", response_model=ServiceInfo)
def service_info() -> ServiceInfo:
    """Return stable service identity without probing dependencies."""
    return ServiceInfo(name="RoboLake", status="running", version=__version__)


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
def health_check(request: Request, response: Response) -> HealthResponse:
    """Report application, PostgreSQL, and object-storage connectivity."""
    health_service = cast(HealthService, request.app.state.health_service)
    report = health_service.check()
    if report.status == "degraded":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status=report.status, components=report.components)
