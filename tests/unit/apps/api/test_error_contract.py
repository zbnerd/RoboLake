"""Stable, actionable, and secret-safe API error contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from apps.api.dependencies import ApiServices
from apps.api.main import create_app
from httpx import ASGITransport, AsyncClient
from robolake.application.health import HealthService
from robolake.domain.errors import (
    ProviderContractError,
    StoredObjectMismatchError,
    UploadConflictError,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@dataclass
class RaisingTransfers:
    error: Exception

    def complete_upload(self, session_id: UUID, etag: str | None = None) -> object:
        raise self.error


def _app(*, transfers: object, registry: object | None = None) -> Any:
    return create_app(
        services=ApiServices(
            health=HealthService([]),
            registry=cast(Any, registry or object()),
            transfers=cast(Any, transfers),
            close=lambda: None,
        )
    )


@pytest.mark.parametrize(
    ("error", "code", "next_action"),
    [
        (
            StoredObjectMismatchError("secret=http://storage.invalid/key?X-Amz-Credential=private"),
            "STORED_OBJECT_MISMATCH",
            "CONTACT_OPERATOR",
        ),
        (UploadConflictError("provider response secret"), "UPLOAD_CONFLICT", "RETRY_PUSH"),
        (
            ProviderContractError(
                "secret=http://storage.invalid/key?uploadId=private&X-Amz-Signature=private"
            ),
            "PROVIDER_CONTRACT_ERROR",
            None,
        ),
    ],
)
async def test_domain_conflicts_use_safe_symbolic_envelope(
    error: Exception, code: str, next_action: str | None
) -> None:
    app = _app(transfers=RaisingTransfers(error))

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/v1/upload-sessions/{uuid4()}/complete",
            json={},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["next_action"] == next_action
    assert "secret" not in response.text
    assert "X-Amz" not in response.text


class ExplodingRegistry:
    calls = 0

    def create_dataset(self, *args: object) -> object:
        self.calls += 1
        raise RuntimeError("postgresql://user:password@private-host/database")


async def test_unexpected_exception_is_generic_and_request_validation_uses_envelope() -> None:
    registry = ExplodingRegistry()
    app = _app(transfers=object(), registry=registry)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        invalid = await client.post(
            "/v1/datasets",
            json={"name": "demo", "unexpected": True},
            headers={"Idempotency-Key": "key"},
        )
        unexpected = await client.post(
            "/v1/datasets",
            json={"name": "demo"},
            headers={"Idempotency-Key": "key"},
        )

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
    assert unexpected.status_code == 500
    assert unexpected.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "password" not in unexpected.text
    assert registry.calls == 1


async def test_body_larger_than_manifest_protocol_limit_is_rejected_before_route() -> None:
    registry = ExplodingRegistry()
    app = _app(transfers=object(), registry=registry)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/datasets",
            content=b"x" * (64 * 1024 * 1024 + 1),
            headers={"Content-Type": "application/json", "Idempotency-Key": "large"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"
    assert registry.calls == 0
