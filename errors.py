"""
Unified API error types, codes, and FastAPI exception handlers.

Every error response uses the same JSON shape::

    {"error": "...", "code": "NOT_FOUND", "hint": "...", "details": {...}}
"""

from __future__ import annotations

import logging
import sqlite3
from enum import StrEnum
from typing import Any, NoReturn

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ai_core import AICoreError

LOGGER = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Stable machine-readable error codes returned in ``ErrorResponse.code``."""

    AI_CORE_FAILURE = "AI_CORE_FAILURE"
    DB_LOCKED = "DB_LOCKED"
    PATH_INVALID = "PATH_INVALID"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SCAN_IN_PROGRESS = "SCAN_IN_PROGRESS"
    SCAN_CANCELLED = "SCAN_CANCELLED"


DEFAULT_HINTS: dict[ErrorCode, str] = {
    ErrorCode.AI_CORE_FAILURE: (
        "AI processing failed. Check backend logs and restart the app if the scan is stuck."
    ),
    ErrorCode.DB_LOCKED: (
        "The database is locked or a constraint was violated. Retry in a moment."
    ),
    ErrorCode.PATH_INVALID: (
        "The folder path is not valid for scanning. Choose an existing photo directory."
    ),
    ErrorCode.VALIDATION_ERROR: (
        "The request was invalid. Check parameters and JSON body."
    ),
    ErrorCode.NOT_FOUND: "The requested resource was not found.",
    ErrorCode.INTERNAL_ERROR: (
        "An unexpected server error occurred. Check backend logs for details."
    ),
    ErrorCode.SCAN_IN_PROGRESS: (
        "A folder scan is already running. Wait for it to finish or poll scan status."
    ),
    ErrorCode.SCAN_CANCELLED: "The scan was cancelled before it completed.",
}


class ErrorResponse(BaseModel):
    """Standard error payload for all API failure responses."""

    error: str = Field(..., description="Human-readable error message.")
    code: ErrorCode = Field(..., description="Stable machine-readable error code.")
    hint: str | None = Field(
        None,
        description="Optional user-facing guidance (safe to show in the UI).",
    )
    details: dict[str, Any] | None = Field(
        None,
        description="Optional structured context (never contains stack traces).",
    )


class PathValidationError(Exception):
    """
    Raised when a user-supplied filesystem path fails security or shape checks.

    Task 1.2.3 will extend validation; this type is registered globally here.
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


def error_response_dict(
    *,
    error: str,
    code: ErrorCode,
    hint: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable ``ErrorResponse`` dict."""
    return ErrorResponse(
        error=error,
        code=code,
        hint=hint if hint is not None else DEFAULT_HINTS.get(code),
        details=details,
    ).model_dump(mode="json")


def raise_api_error(
    *,
    status_code: int,
    error: str,
    code: ErrorCode,
    hint: str | None = None,
    details: dict[str, Any] | None = None,
) -> NoReturn:
    """Raise ``HTTPException`` whose ``detail`` is a standard ``ErrorResponse`` dict."""
    raise HTTPException(
        status_code=status_code,
        detail=error_response_dict(
            error=error,
            code=code,
            hint=hint,
            details=details,
        ),
    )


def _json_response(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


def _code_for_http_status(status_code: int) -> ErrorCode:
    if status_code == status.HTTP_404_NOT_FOUND:
        return ErrorCode.NOT_FOUND
    if status_code == status.HTTP_409_CONFLICT:
        return ErrorCode.SCAN_IN_PROGRESS
    if status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY}:
        return ErrorCode.VALIDATION_ERROR
    if status_code >= 500:
        return ErrorCode.INTERNAL_ERROR
    return ErrorCode.VALIDATION_ERROR


def _normalize_http_exception_detail(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail and "code" in detail:
        return detail
    message = str(detail)
    return error_response_dict(
        error=message,
        code=_code_for_http_status(exc.status_code),
    )


async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    """Wrap legacy string ``HTTPException.detail`` values in ``ErrorResponse``."""
    return _json_response(exc.status_code, _normalize_http_exception_detail(exc))


async def validation_exception_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Map FastAPI/Pydantic request validation failures to ``VALIDATION_ERROR``."""
    sanitized_errors: list[dict[str, Any]] = []
    for item in exc.errors():
        entry = dict(item)
        ctx = entry.get("ctx")
        if isinstance(ctx, dict):
            entry["ctx"] = {key: str(value) for key, value in ctx.items()}
        sanitized_errors.append(entry)

    return _json_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_response_dict(
            error="Request validation failed",
            code=ErrorCode.VALIDATION_ERROR,
            details={"errors": sanitized_errors},
        ),
    )


async def aicore_exception_handler(_request: Request, exc: AICoreError) -> JSONResponse:
    """Map uncaught ``AICoreError`` subclasses to ``AI_CORE_FAILURE``."""
    LOGGER.error(
        "AICoreError: %s",
        exc,
        extra={"context": exc.context, "error_type": type(exc).__name__},
        exc_info=True,
    )
    details: dict[str, Any] = dict(exc.context)
    if exc.code and exc.code != ErrorCode.AI_CORE_FAILURE:
        details.setdefault("ai_code", exc.code)
    return _json_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_response_dict(
            error=str(exc),
            code=ErrorCode.AI_CORE_FAILURE,
            hint=exc.hint,
            details=details or None,
        ),
    )


async def path_validation_exception_handler(
    _request: Request,
    exc: PathValidationError,
) -> JSONResponse:
    return _json_response(
        status.HTTP_400_BAD_REQUEST,
        error_response_dict(
            error=exc.message,
            code=ErrorCode.PATH_INVALID,
            details=exc.details or None,
        ),
    )


async def integrity_error_handler(
    _request: Request,
    exc: sqlite3.IntegrityError,
) -> JSONResponse:
    LOGGER.error("sqlite3.IntegrityError: %s", exc, exc_info=True)
    return _json_response(
        status.HTTP_409_CONFLICT,
        error_response_dict(
            error="Database integrity constraint violated",
            code=ErrorCode.DB_LOCKED,
            details={"sqlite_message": str(exc)},
        ),
    )


async def unhandled_exception_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    LOGGER.exception("Unhandled exception: %s", exc)
    return _json_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_response_dict(
            error="Internal server error",
            code=ErrorCode.INTERNAL_ERROR,
        ),
    )


def register_exception_handlers(application: FastAPI) -> None:
    """Attach global exception handlers that emit ``ErrorResponse`` JSON."""
    application.add_exception_handler(HTTPException, http_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(AICoreError, aicore_exception_handler)
    application.add_exception_handler(PathValidationError, path_validation_exception_handler)
    application.add_exception_handler(sqlite3.IntegrityError, integrity_error_handler)
    application.add_exception_handler(Exception, unhandled_exception_handler)
