"""Shared safety limits, validators and response-header helpers.

These helpers exist so that every endpoint enforces the same bounds:
  * valid HTTP status codes (200-599; 1xx is not emittable as a final response)
  * maximum delays
  * maximum redirect chain length
  * header input validation (no hop-by-hop or framing header overrides)
"""

from __future__ import annotations

import re

from fastapi import HTTPException

# 1xx codes cannot be emitted as a final response by HTTP/1.1 servers, so the
# simulator supports the full "final response" range only.
MIN_STATUS = 200
MAX_STATUS = 599

# Statuses for which HTTP semantics forbid a response body.
NO_BODY_STATUSES: frozenset[int] = frozenset({204, 304})

REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})

HOP_BY_HOP_HEADERS: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "content-encoding",
        "content-range",
    }
)

_SIZES: dict[str, int] = {
    "b": 1,
    "": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024 * 1024,
    "mb": 1024 * 1024,
    "g": 1024 * 1024 * 1024,
    "gb": 1024 * 1024 * 1024,
    "t": 1024**4,
    "tb": 1024**4,
}
_SIZE_RE = re.compile(r"^\s*(\d+)\s*([kmgt]?b?)\s*$", re.IGNORECASE)

_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,100}$")


def parse_size(value: str) -> int:
    """Parse a human size like '1kb', '10MB', '512' into bytes.

    Raises ValueError for malformed input.
    """
    match = _SIZE_RE.match(value)
    if not match:
        raise ValueError(f"invalid size: {value!r} (use e.g. 512, 1kb, 10mb)")
    number, unit = match.groups()
    return int(number) * _SIZES[unit.lower() or "b"]


def require_status(code: int) -> int:
    """Validate *code* is a emittable final status; raise 400 otherwise."""
    if not MIN_STATUS <= code <= MAX_STATUS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_status",
                "message": f"status must be between {MIN_STATUS} and {MAX_STATUS}",
            },
        )
    return code


def require_delay(ms: int, max_delay_ms: int) -> int:
    """Validate *ms* is non-negative and below the configured ceiling."""
    if ms < 0:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_delay", "message": "delay must be >= 0"}
        )
    if ms > max_delay_ms:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "delay_exceeds_limit",
                "message": f"delay {ms}ms exceeds configured maximum {max_delay_ms}ms",
            },
        )
    return ms


def parse_http_headers(header_params: list[str] | None) -> dict[str, str]:
    """Parse repeated `header=Name: value` query parameters into a header dict.

    Rejects control characters and hop-by-hop / framing headers so clients can
    never override transport framing with the response builder.
    """
    parsed: dict[str, str] = {}
    for raw in header_params or []:
        name, sep, value = raw.partition(":")
        name = name.strip()
        value = value.strip()
        if not sep or not _HEADER_NAME_RE.match(name) or not value:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_header",
                    "message": f"header must be 'Name: value', got {raw!r}",
                },
            )
        if name.lower() in HOP_BY_HOP_HEADERS:
            raise HTTPException(
                status_code=400,
                detail={"error": "forbidden_header", "message": f"header {name!r} is not allowed"},
            )
        # Spaces and tabs are legal in field values (RFC 9110 §5.5); only
        # control characters (including CR/LF) are rejected.
        if any((ord(ch) < 32 and ch != "\t") or ord(ch) == 127 for ch in value):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_header",
                    "message": "header value contains control characters",
                },
            )
        parsed[name] = value
    return parsed
