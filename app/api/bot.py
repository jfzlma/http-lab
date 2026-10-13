"""Bot-signal heuristics endpoint: /bot."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..services.bot_signals import detect_bot_signals
from ..services.fingerprint import http_version, normalized_headers, tls_analysis

router = APIRouter(tags=["inspection"])


@router.get(
    "/bot",
    summary="Report observable automation signals (heuristics only)",
    description=(
        "Deliberately does not declare whether the client is a bot. Returns a list of "
        "observable heuristics with qualitative confidence. No score is computed."
    ),
)
async def bot_endpoint(request: Request) -> dict[str, Any]:
    headers = normalized_headers(request)
    tls = tls_analysis(request)
    result = detect_bot_signals(headers, http_version(request), bool(tls.get("available")))
    result["client_ip"] = request.state.client_ip
    return result
