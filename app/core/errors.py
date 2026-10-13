"""Uniform JSON error envelope.

Every error HTTP Lab produces itself (validation failures, safety limits,
rate limits, unhandled exceptions) is rendered as::

    {"error": "<machine_code>", "message": "<human text>", "request_id": "<id>", ...}

Endpoints raise ``HTTPException`` with a dict ``detail``; its keys are merged
into the top level of the envelope. Simulated statuses (``/status/500`` etc.)
are ordinary responses and never pass through these handlers.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("httplab.errors")

_DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    429: "rate_limit_exceeded",
    503: "service_unavailable",
}


def error_body(request: Request | None, status: int, detail: Any) -> dict[str, Any]:
    """Build the error envelope for *detail* (a dict or a plain message)."""
    if isinstance(detail, dict):
        body: dict[str, Any] = dict(detail)
    else:
        body = {"message": str(detail)}
    body.setdefault("error", _DEFAULT_CODES.get(status, "http_error"))
    body.setdefault("message", body["error"].replace("_", " "))
    request_id = getattr(request.state, "request_id", None) if request is not None else None
    if request_id:
        body["request_id"] = request_id
    return body


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(
        error_body(request, exc.status_code, exc.detail),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"location": list(err.get("loc", ())), "message": err.get("msg", "")}
        for err in exc.errors()
    ]
    first = errors[0] if errors else {"location": [], "message": "invalid request"}
    where = ".".join(str(p) for p in first["location"])
    detail = {
        "error": "validation_error",
        "message": f"{where}: {first['message']}" if where else first["message"],
        "errors": errors,
    }
    return JSONResponse(error_body(request, 422, detail), status_code=422)


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled error on %s %s (request_id=%s)",
        request.method,
        request.url.path,
        getattr(request.state, "request_id", "-"),
    )
    detail = {"error": "internal_error", "message": "an unexpected error occurred"}
    return JSONResponse(error_body(request, 500, detail), status_code=500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
