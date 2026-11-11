"""One error shape for every API response.

    {"error": {"code": "feed_not_found", "message": "...", "retry_after": 30}}

`code` is a stable identifier the client maps to its own localized text.
`message` is for developers only. `retry_after` (seconds) appears only when
retrying later can succeed, and is mirrored in the Retry-After header.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class FieldError(BaseModel):
    loc: list[str | int]
    msg: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    retry_after: int | None = None
    # Present only for `invalid_request`: which inputs failed validation.
    fields: list[FieldError] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class AppError(Exception):
    """Raise anywhere in a request to return a structured error."""

    def __init__(
        self, status: int, code: str, message: str, *, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


def error_body(
    code: str,
    message: str,
    *,
    retry_after: int | None = None,
    fields: list[FieldError] | None = None,
) -> dict[str, Any]:
    detail = ErrorDetail(code=code, message=message, retry_after=retry_after, fields=fields)
    return {"error": detail.model_dump(exclude_none=True)}


def error_response(
    status: int, code: str, message: str, *, retry_after: int | None = None
) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(
        status_code=status,
        content=error_body(code, message, retry_after=retry_after),
        headers=headers,
    )


# Codes added automatically, by how much access a route requires.
_ACCESS_ERRORS: dict[str, tuple[tuple[int, str], ...]] = {
    "public": (),
    "server": ((401, "invalid_server_token"), (429, "too_many_attempts")),
    "user": (
        (401, "invalid_server_token"),
        (429, "too_many_attempts"),
        (401, "not_authenticated"),
    ),
}


def error_responses(
    *errors: tuple[int, str], access: Literal["public", "server", "user"] = "user"
) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` for a route: each status lists the codes it may carry.

    `access` adds the gate's codes: "server" needs the deployment token, "user"
    also a signed-in user.
    """
    by_status: dict[int, list[str]] = defaultdict(list)
    # Declaring 422 here also stops FastAPI adding its own {"detail": ...} schema.
    for status, code in (*_ACCESS_ERRORS[access], (422, "invalid_request"), *errors):
        if code not in by_status[status]:
            by_status[status].append(code)
    return {
        status: {
            "model": ErrorResponse,
            "description": "Error codes: " + ", ".join(f"`{code}`" for code in codes),
        }
        for status, codes in sorted(by_status.items())
    }


_HTTP_CODES = {
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    415: "unsupported_media_type",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, retry_after=exc.retry_after)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Inputs can contain passwords: report locations and messages, never values.
        fields = [
            FieldError(loc=[part for part in error["loc"]], msg=error["msg"])
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body("invalid_request", "The request is invalid.", fields=fields),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        message = exc.detail if isinstance(exc.detail, str) else code
        return error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error: %s %s", request.method, request.url.path)
        return error_response(500, "internal_error", "An unexpected error occurred.")
