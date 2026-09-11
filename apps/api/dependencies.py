"""Typed service container and route dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from fastapi import Request
from robolake.application.health import HealthService
from robolake.application.registry import RegistryService
from robolake.application.transfers import TransferService


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Owned API application services and their deterministic cleanup."""

    health: HealthService
    registry: RegistryService
    transfers: TransferService
    close: Callable[[], None]


def get_services(request: Request) -> ApiServices:
    """Read the application-scoped typed service container."""
    return cast(ApiServices, request.app.state.services)


def get_registry(request: Request) -> RegistryService:
    return get_services(request).registry


def get_transfers(request: Request) -> TransferService:
    return get_services(request).transfers
