"""Framework-free dependency health orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ComponentStatus = Literal["ok", "down"]
OverallStatus = Literal["ok", "degraded"]


class HealthProbe(Protocol):
    """A dependency probe that raises when its dependency is unavailable."""

    @property
    def name(self) -> str:
        """Stable component name used in health responses."""

    def check(self) -> None:
        """Verify dependency connectivity."""


@dataclass(frozen=True)
class HealthReport:
    """Stable health state with no provider exception details."""

    status: OverallStatus
    components: dict[str, ComponentStatus]


class HealthService:
    """Translate dependency failures into a safe aggregate report."""

    def __init__(self, probes: list[HealthProbe]) -> None:
        self._probes = tuple(probes)

    def check(self) -> HealthReport:
        components: dict[str, ComponentStatus] = {"application": "ok"}
        status: OverallStatus = "ok"

        for probe in self._probes:
            try:
                probe.check()
            except Exception:  # noqa: BLE001 - application boundary intentionally translates failures.
                components[probe.name] = "down"
                status = "degraded"
            else:
                components[probe.name] = "ok"

        return HealthReport(status=status, components=components)
