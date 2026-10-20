"""Shared translatable HTTP errors (TR-8, spec 9.6)."""

import re
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from api.utils.logger import logger

_KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z")
_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    429: "too_many_requests",
    500: "internal_error",
    503: "service_unavailable",
}


class ErrorField(BaseModel):
    field: str
    code: str


class ErrorDetail(BaseModel):
    code: str
    fields: list[ErrorField] | None = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


def error_response(
    request_id: str,
    status_code: int,
    code: str,
    *,
    fields: list[ErrorField] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code=code,
            fields=(fields or []) if status_code == 422 else None,
            request_id=request_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=headers,
    )


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, HTTPException):
        return await unhandled_error_handler(request, exc)
    fallback = "bad_request" if 400 <= exc.status_code < 500 else "internal_error"
    code = _HTTP_CODES.get(exc.status_code, fallback)
    # Framework defaults are English sentences. Only explicit translation
    # keys may cross the boundary, and server errors never expose details.
    if exc.status_code < 500 and isinstance(exc.detail, str) and _KEY.fullmatch(exc.detail):
        code = exc.detail
    return error_response(request.state.request_id, exc.status_code, code, headers=exc.headers)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        return await unhandled_error_handler(request, exc)
    fields = []
    for error in exc.errors():
        location = error["loc"]
        path = (
            location[1:]
            if location and location[0] in {"body", "query", "path", "header", "cookie"}
            else location
        )
        fields.append(ErrorField(field=".".join(str(part) for part in path) or "body", code=error["type"]))
    return error_response(request.state.request_id, 422, "validation_failed", fields=fields)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Exception messages and tracebacks can contain user input or credentials.
    logger.error("unhandled request error", extra={"error": type(exc).__name__})
    return error_response(request.state.request_id, 500, "internal_error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
