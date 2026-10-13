"""Configurable per-client rate limit simulator: /rate-limit."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..core.rate_limiter import retry_after_seconds

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simulators"])


@router.get(
    "/rate-limit",
    summary="Simulate a per-client rate limit",
    description=(
        "Allows `requests` requests per `window` seconds per client IP. Once exceeded it "
        "returns 429 with Retry-After and X-RateLimit-* headers. The hosted service can "
        "additionally enforce a global cap via RATE_LIMIT_ENABLED."
    ),
)
async def rate_limit_endpoint(
    request: Request,
    requests: int = Query(5, ge=1, le=10_000, description="Number of allowed requests"),
    window: int = Query(60, ge=1, le=3600, description="Window in seconds"),
) -> JSONResponse:
    limiter = request.app.state.rate_limiter
    key = f"sim:{request.state.client_ip}:{requests}:{window}"
    try:
        state = await limiter.check(key, requests, float(window))
    except Exception as exc:
        # The limiter backend (Redis) is down: say so instead of a generic 500.
        logger.warning("rate limiter backend unavailable", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "rate_limiter_unavailable",
                "message": "the rate limit backend is temporarily unavailable",
            },
            headers={"Retry-After": "5"},
        ) from exc

    headers: dict[str, Any] = {
        "X-RateLimit-Limit": str(requests),
        "X-RateLimit-Remaining": str(state.remaining),
        "X-RateLimit-Reset": str(int(state.reset_at)),
    }
    if not state.allowed:
        headers["Retry-After"] = str(retry_after_seconds(state.retry_after))
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": "too many requests",
                "retry_after": headers["Retry-After"],
            },
            headers=headers,
        )
    return JSONResponse(
        {
            "allowed": True,
            "limit": requests,
            "remaining": state.remaining,
            "window_seconds": window,
        },
        headers=headers,
    )
