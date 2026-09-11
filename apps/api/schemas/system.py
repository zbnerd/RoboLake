"""System endpoint schemas."""

from typing import Literal

from pydantic import BaseModel


class ServiceInfo(BaseModel):
    """Public service identity."""

    name: Literal["RoboLake"]
    status: Literal["running"]
    version: str


class HealthResponse(BaseModel):
    """Safe aggregate application and dependency health."""

    status: Literal["ok", "degraded"]
    components: dict[str, Literal["ok", "down"]]
