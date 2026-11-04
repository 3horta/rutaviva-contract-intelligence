"""RFC 9457 Problem Details for every error the API returns.

Every error body has the same shape, so the frontend handles errors in one place:

    {"type": ..., "title": ..., "status": ..., "detail": ..., "trace_id": ...}

plus `errors` (field, message, type) for validation errors. `trace_id` lets a user report
an error that can be found in the traces; it is null when no trace is being recorded.
Internal details (exception messages, stack traces, input values) never reach the client.
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import tracing
from app.core.errors import AppError, Unauthorized

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"


def problem_body(
    status: int, title: str, detail: str, type_: str = "about:blank"
) -> dict[str, Any]:
    """The Problem Details object itself; also sent as the `error` event of a stream."""
    return {
        "type": type_,
        "title": title,
        "status": status,
        "detail": detail,
        "trace_id": tracing.current_trace_id(),
    }


def problem_response(
    status: int,
    title: str,
    detail: str,
    type_: str = "about:blank",
    *,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = problem_body(status, title, detail, type_)
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_CONTENT_TYPE, headers=headers)


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    headers = None
    if isinstance(exc, Unauthorized):
        # RFC 6750: a 401 tells the client which scheme to use.
        headers = {"WWW-Authenticate": "Bearer"}
    return problem_response(exc.status, exc.title, exc.detail, exc.type, headers=headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only location, message and type: the rejected input could be document content.
    errors = [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "message": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    return problem_response(
        422,
        "Validation error",
        "The request is not valid; see `errors`.",
        "/problems/validation-error",
        extra={"errors": errors},
    )


async def _http_exception(request: Request, exc: Exception) -> JSONResponse:
    # Framework errors: unknown route (404), wrong method (405), etc.
    assert isinstance(exc, StarletteHTTPException)
    title = HTTPStatus(exc.status_code).phrase
    detail = exc.detail if isinstance(exc.detail, str) else title
    return problem_response(exc.status_code, title, detail, headers=exc.headers)


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    # The stack trace goes to the logs (and the span); the client gets a generic message.
    logger.exception(
        "api.unhandled_error",
        extra={"path": request.url.path, "error_type": type(exc).__name__},
    )
    return problem_response(500, "Internal error", "An unexpected error occurred.")


def install_problem_handlers(app: FastAPI) -> None:
    """Register exception handlers that render `AppError` (and validation and unexpected
    errors) as `application/problem+json` with the current `trace_id`."""
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    # Starlette runs this one from its outermost middleware and then re-raises the
    # exception, so the server still logs it; the client only sees the 500 problem.
    app.add_exception_handler(Exception, _unhandled_error)
