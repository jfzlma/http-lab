"""ASGI middleware for request context, guards, and redacted request logging.

Responsibilities (in order):
  1. Resolve the real client IP honoring ``TRUSTED_PROXY_COUNT``.
  2. Reject requests whose declared Content-Length exceeds the configured cap.
  3. Apply the global per-IP rate limit when ``RATE_LIMIT_ENABLED``.
  4. Tag every response with ``X-Request-Id`` (an inbound id is honoured only
     when it is short and made of safe characters).
  5. Log requests (method, path, redacted query, status, duration, client IP)
     when ``LOG_REQUESTS`` is enabled. Bodies are never logged.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from ..config import Settings
from ..logging_conf import redact_headers, redact_query_string
from .analytics import Analytics
from .errors import error_body
from .rate_limiter import retry_after_seconds

logger = logging.getLogger("httplab.request")

# x-request-id is omitted: the resolved, validated id is logged separately.
_ALLOWED_LOG_HEADERS = ("user-agent", "host", "content-type")
# Liveness/readiness probes must never be throttled, or a strict global limit
# would make orchestrators restart a healthy container.
_RATE_LIMIT_EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def resolve_request_id(inbound: str | None) -> str:
    """Reuse a well-formed inbound ``X-Request-Id``; otherwise mint a new one."""
    if inbound and _REQUEST_ID_RE.match(inbound):
        return inbound
    return uuid.uuid4().hex


def resolve_client_ip(request: Request, trusted_proxy_count: int) -> str:
    """Return the client IP, walking X-Forwarded-For if proxies are trusted.

    With ``trusted_proxy_count == 0`` (direct exposure) the socket peer address
    is authoritative and forwarding headers are treated as client-supplied data.

    Each trusted proxy *appends* the address it received the connection from, so
    behind N proxies the client is the N-th entry from the right. Anything further
    left was supplied by the client itself and is never trusted.
    """
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_count <= 0:
        return peer
    xff = request.headers.get("x-forwarded-for")
    if not xff:
        return peer
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if not parts:
        return peer
    # Fewer entries than proxies means a misconfigured count; the leftmost entry
    # is then the furthest hop we can see.
    return parts[-trusted_proxy_count] if len(parts) >= trusted_proxy_count else parts[0]


def _error_response(request: Request, status: int, detail: dict[str, object]) -> Response:
    return JSONResponse(error_body(request, status, detail), status_code=status)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """See module docstring."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.analytics = Analytics(settings)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        request.state.request_id = resolve_request_id(request.headers.get("x-request-id"))
        request.state.client_ip = resolve_client_ip(request, self.settings.trusted_proxy_count)

        try:
            response = await self._guard(request) or await call_next(request)
        except Exception:
            self._log(request, 500, start)
            raise
        response.headers.setdefault("X-Request-Id", request.state.request_id)
        self._log(request, response.status_code, start)
        return response

    async def _guard(self, request: Request) -> Response | None:
        """Return an early rejection response, or None to let the request through."""
        length_header = request.headers.get("content-length")
        if length_header is not None:
            try:
                declared = int(length_header)
            except ValueError:
                declared = -1
            if declared > self.settings.max_request_body_bytes:
                return _error_response(
                    request,
                    413,
                    {
                        "error": "payload_too_large",
                        "message": (
                            f"request body exceeds maximum of {self.settings.max_request_body_mb}MB"
                        ),
                    },
                )

        limiter = getattr(request.app.state, "rate_limiter", None)
        if (
            not self.settings.rate_limit_enabled
            or limiter is None
            or request.url.path in _RATE_LIMIT_EXEMPT_PATHS
        ):
            return None
        try:
            state = await limiter.check(
                f"global:{request.state.client_ip}",
                self.settings.rate_limit_requests,
                float(self.settings.rate_limit_window),
            )
        except Exception:
            # Fail open: a broken limiter backend (e.g. Redis down) must not take
            # the whole service offline.
            logger.warning("global rate limiter unavailable; allowing request", exc_info=True)
            return None
        if state.allowed:
            return None
        retry_after = retry_after_seconds(state.retry_after)
        response = _error_response(
            request,
            429,
            {
                "error": "rate_limit_exceeded",
                "message": "too many requests",
                "retry_after": retry_after,
            },
        )
        response.headers["Retry-After"] = str(retry_after)
        return response

    def _log(self, request: Request, status: int, start: float) -> None:
        self.analytics.record(request, status, (time.perf_counter() - start) * 1000)
        if not self.settings.log_requests:
            return
        query = redact_query_string(request.url.query)
        selected = redact_headers(
            {
                name: value
                for name, value in request.headers.items()
                if name.lower() in _ALLOWED_LOG_HEADERS
            }
        )
        logger.info(
            "method=%s path=%s%s status=%d duration_ms=%.1f ip=%s request_id=%s headers=%s",
            request.method,
            request.url.path,
            f"?{query}" if query else "",
            status,
            (time.perf_counter() - start) * 1000,
            request.state.client_ip,
            request.state.request_id,
            selected,
        )
