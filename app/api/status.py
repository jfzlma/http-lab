"""Status code simulator — return any valid final HTTP status code."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from ..core.concurrency import guarded_sleep
from ..core.limits import NO_BODY_STATUSES, parse_http_headers, require_delay, require_status

router = APIRouter(tags=["status"])

_STATUS_DESCRIPTION = """
Simulate an arbitrary final HTTP status code (200-599).

Query options:
* `message` — free-text message included in the default JSON body
* `body` — raw response body instead of the default JSON payload
* `delay` — milliseconds to wait before responding (bounded by MAX_DELAY_MS)
* `header` — repeatable `Name: value` pair added to the response

Statuses 204 and 304 are returned without a body (per HTTP semantics).
"""


@router.api_route(
    "/status/{code}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    summary="Simulate an HTTP status code",
    description=_STATUS_DESCRIPTION,
)
async def status_endpoint(
    request: Request,
    code: int,
    message: str | None = Query(
        None, max_length=500, description="Optional message in the default JSON body"
    ),
    body: str | None = Query(
        None, description="Raw response body (replaces the default JSON payload)"
    ),
    delay: int = Query(0, ge=0, description="Delay in milliseconds before responding"),
    header: list[str] | None = Query(None, description="Repeatable 'Name: value' response header"),
) -> Response:
    settings = request.app.state.settings
    code = require_status(code)
    delay = require_delay(delay, settings.max_delay_ms)
    extra_headers = parse_http_headers(header)

    if delay:
        await guarded_sleep(request.app.state.slow_semaphore, delay)

    if code in NO_BODY_STATUSES:
        return Response(status_code=code, headers=extra_headers)

    if body is not None:
        stripped = body.strip()
        if stripped.startswith(("{", "[")):
            return Response(
                content=body, status_code=code, media_type="application/json", headers=extra_headers
            )
        return Response(
            content=body,
            status_code=code,
            media_type="text/plain; charset=utf-8",
            headers=extra_headers,
        )

    payload = {
        "status": code,
        "message": message,
        "delay_ms": delay,
        "path": request.url.path,
        "method": request.method,
    }
    return JSONResponse(payload, status_code=code, headers=extra_headers)
