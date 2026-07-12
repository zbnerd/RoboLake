"""Dataset control-plane routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from robolake.application.registry import RegistryService
from robolake.domain.identifiers import DatasetName

from apps.api.dependencies import get_registry
from apps.api.schemas.datasets import DatasetCreateRequest, DatasetResponse

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post("", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
def create_dataset(
    request: DatasetCreateRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    registry: Annotated[RegistryService, Depends(get_registry)],
) -> DatasetResponse:
    """Idempotently create or resolve one normalized Dataset."""
    record = registry.create_dataset(DatasetName.parse(request.name), idempotency_key)
    return DatasetResponse(id=record.id, name=record.name.value)
