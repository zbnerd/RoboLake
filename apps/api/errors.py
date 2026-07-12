"""Safe translation of domain and boundary failures to HTTP."""

from __future__ import annotations

import logging
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from robolake.domain.errors import (
    ContentConflictError,
    IdempotencyConflictError,
    IllegalTransitionError,
    InvalidCursorError,
    InvalidDatasetName,
    InvalidDatasetReference,
    InvalidDigestError,
    ManifestMismatchError,
    NotFoundError,
    PathCollisionError,
    RoboLakeError,
    SourceChangedError,
    StoredObjectMismatchError,
    UnsafeFileTypeError,
    UnsafePathError,
    UnsupportedFileSizeError,
    UnsupportedPlatformError,
    UploadConflictError,
)
from sqlalchemy.exc import SQLAlchemyError

from apps.api.middleware.body_limit import RequestBodyTooLarge
from apps.api.schemas.errors import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)

_VALIDATION_ERRORS = (
    InvalidCursorError,
    InvalidDatasetName,
    InvalidDatasetReference,
    InvalidDigestError,
    ManifestMismatchError,
    PathCollisionError,
    SourceChangedError,
    UnsafeFileTypeError,
    UnsafePathError,
    UnsupportedFileSizeError,
    UnsupportedPlatformError,
)
_CONFLICT_ERRORS = (
    ContentConflictError,
    IdempotencyConflictError,
    IllegalTransitionError,
    StoredObjectMismatchError,
    UploadConflictError,
)


def _response(
    status_code: int,
    code: str,
    message: str,
    next_action: str | None = None,
    *,
    correlation_id: str | None = None,
) -> JSONResponse:
    headers = {"X-Correlation-ID": correlation_id} if correlation_id is not None else None
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message, next_action=next_action))
    return JSONResponse(status_code=status_code, content=payload.model_dump(), headers=headers)


def install_exception_handlers(app: FastAPI) -> None:
    """Install the only boundaries allowed to translate broad infrastructure failures."""

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return _response(422, "INVALID_REQUEST", "Request fields are invalid.")

    @app.exception_handler(RequestBodyTooLarge)
    async def body_limit_handler(_: Request, __: RequestBodyTooLarge) -> JSONResponse:
        return _response(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "REQUEST_BODY_TOO_LARGE",
            "Request body exceeds the manifest protocol limit.",
        )

    @app.exception_handler(RoboLakeError)
    async def domain_handler(_: Request, error: RoboLakeError) -> JSONResponse:
        if isinstance(error, NotFoundError):
            return _response(404, error.code, "Requested RoboLake resource was not found.")
        if isinstance(error, StoredObjectMismatchError):
            return _response(
                409,
                error.code,
                "Stored object does not match its immutable content address.",
                "CONTACT_OPERATOR",
            )
        if isinstance(error, UploadConflictError):
            return _response(
                409,
                error.code,
                "Upload outcome is not visible yet; rerun push.",
                "RETRY_PUSH",
            )
        if isinstance(error, _VALIDATION_ERRORS):
            return _response(422, error.code, str(error))
        if isinstance(error, _CONFLICT_ERRORS):
            return _response(409, error.code, str(error))
        return _response(409, error.code, "RoboLake operation could not be completed safely.")

    @app.exception_handler(ClientError)
    @app.exception_handler(BotoCoreError)
    async def storage_handler(_: Request, error: Exception) -> JSONResponse:
        correlation_id = uuid4().hex
        logger.warning(
            "upstream storage failure correlation_id=%s error_type=%s",
            correlation_id,
            type(error).__name__,
        )
        return _response(
            status.HTTP_502_BAD_GATEWAY,
            "OBJECT_STORAGE_UNAVAILABLE",
            "Object storage request failed.",
            "RETRY_PUSH",
            correlation_id=correlation_id,
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_handler(_: Request, error: SQLAlchemyError) -> JSONResponse:
        correlation_id = uuid4().hex
        logger.warning(
            "database failure correlation_id=%s error_type=%s",
            correlation_id,
            type(error).__name__,
        )
        return _response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "REGISTRY_UNAVAILABLE",
            "Registry database is unavailable.",
            "RETRY_PUSH",
            correlation_id=correlation_id,
        )

    @app.exception_handler(Exception)
    async def unexpected_handler(_: Request, error: Exception) -> JSONResponse:
        correlation_id = uuid4().hex
        logger.error(
            "unexpected API failure correlation_id=%s error_type=%s",
            correlation_id,
            type(error).__name__,
        )
        return _response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "Unexpected server error.",
            correlation_id=correlation_id,
        )
