from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["system"])


class ServiceInfo(BaseModel):
    name: str
    status: Literal["running"]


class HealthStatus(BaseModel):
    status: Literal["ok"]


@router.get("/", response_model=ServiceInfo)
async def service_info() -> ServiceInfo:
    return ServiceInfo(name="RoboLake", status="running")


@router.get("/health", response_model=HealthStatus)
async def health_check() -> HealthStatus:
    return HealthStatus(status="ok")
