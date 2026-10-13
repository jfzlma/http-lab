"""Server-side request fingerprint endpoint: /fingerprint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..services.fingerprint import analyze_request

router = APIRouter(tags=["inspection"])


@router.get(
    "/fingerprint",
    summary="Analyze observable HTTP/TLS/header characteristics",
    description=(
        "Reports only what this deployment can observe. TLS fields appear only when the "
        "application terminates TLS itself; JA3/JA4 are never fabricated. Client hints, "
        "fetch metadata and accept/compression headers are reported when present."
    ),
)
async def fingerprint_endpoint(request: Request) -> dict[str, Any]:
    analysis = analyze_request(request)
    analysis["client_ip"] = request.state.client_ip
    return analysis
