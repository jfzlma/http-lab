"""Behaviour simulators: delay, redirect, content types, malformed responses,
response sizes, flaky responses, and the generic response builder."""

from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..core.concurrency import guarded_sleep
from ..core.limits import (
    NO_BODY_STATUSES,
    REDIRECT_STATUSES,
    parse_http_headers,
    parse_size,
    require_delay,
    require_status,
)

router = APIRouter(tags=["simulators"])


# ---------------------------------------------------------------- delay


@router.get("/delay/random", summary="Delay for a random duration within [min, max]")
async def delay_random(
    request: Request,
    min: int = Query(100, ge=0, description="Minimum delay in ms"),
    max: int = Query(2000, ge=0, description="Maximum delay in ms"),
) -> JSONResponse:
    settings = request.app.state.settings
    require_delay(max, settings.max_delay_ms)
    if min > max:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_range", "message": "min must be <= max"}
        )
    ms = random.randint(min, max)
    await guarded_sleep(request.app.state.slow_semaphore, ms)
    return JSONResponse(
        {"slept_ms": ms, "min": min, "max": max, "now": datetime.now(UTC).isoformat()}
    )


@router.get("/delay/{ms}", summary="Delay for a fixed duration before responding")
async def delay_fixed(request: Request, ms: int) -> JSONResponse:
    settings = request.app.state.settings
    ms = require_delay(ms, settings.max_delay_ms)
    await guarded_sleep(request.app.state.slow_semaphore, ms)
    return JSONResponse({"slept_ms": ms, "now": datetime.now(UTC).isoformat()})


# ---------------------------------------------------------------- redirect


@router.get("/redirect/{count}", summary="Generate a redirect chain")
async def redirect_chain(
    request: Request,
    count: int,
    status: int = Query(302, description="Redirect status: 301, 302, 303, 307, 308"),
    to: str = Query("/anything", description="Final relative target path (same-origin only)"),
) -> Response:
    settings = request.app.state.settings
    if count < 0 or count > settings.max_redirects:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "redirect_chain_too_long",
                "message": f"count must be between 0 and {settings.max_redirects}",
            },
        )
    if status not in REDIRECT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_status",
                "message": f"redirect status must be one of {sorted(REDIRECT_STATUSES)}",
            },
        )
    if not to.startswith("/") or to.startswith("//") or "://" in to:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsafe_target",
                "message": "redirect target must be a same-origin relative path",
            },
        )

    # /redirect/{n} issues exactly n redirects; /redirect/0 is the end of the chain.
    if count == 0:
        return JSONResponse({"redirected": False, "hops": 0, "final_target": to})
    if count == 1:
        location = to
        body = {"redirected": True, "hops": 1, "final_target": to, "status": status}
    else:
        location = f"/redirect/{count - 1}?status={status}&to={urllib.parse.quote(to, safe='')}"
        body = {
            "redirected": True,
            "hops": count,
            "final_target": to,
            "status": status,
            "note": "Follow this chain; each hop re-enters /redirect with one fewer hop.",
        }
    return JSONResponse(
        body,
        status_code=status,
        headers={"Location": location, "X-Redirect-Hops": str(count)},
    )


# ---------------------------------------------------------------- content

CONTENT_TYPES: dict[str, tuple[str, bytes]] = {
    "json": (
        "application/json",
        json.dumps(
            {
                "lab": "http-lab",
                "format": "json",
                "message": "hello",
                "generated_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ).encode(),
    ),
    "xml": (
        "application/xml",
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b"<response><status>ok</status><lab>http-lab</lab></response>\n",
    ),
    "html": (
        "text/html; charset=utf-8",
        b"<!doctype html><html><head><title>http-lab</title></head>"
        b"<body><h1>http-lab</h1><p>hello from http-lab</p></body></html>\n",
    ),
    "text": ("text/plain; charset=utf-8", b"hello from http-lab\n"),
    "csv": ("text/csv; charset=utf-8", b"lab,format,message\nhttp-lab,csv,hello\n"),
    "octet-stream": ("application/octet-stream", bytes(range(256)) * 2),
    "problem+json": (
        "application/problem+json",
        json.dumps(
            {
                "type": "about:blank",
                "title": "Simulated Problem",
                "status": 400,
                "detail": "RFC 7807 problem details payload",
                "lab": "http-lab",
            },
            indent=2,
        ).encode(),
    ),
}


def _json_sample() -> bytes:
    payload = {
        "lab": "http-lab",
        "format": "json",
        "message": "hello",
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return json.dumps(payload, indent=2).encode()


CONTENT_ALIASES = {"plain": "text", "octetstream": "octet-stream", "problem-json": "problem+json"}


@router.get("/content/{fmt}", summary="Return a body in a specific content type")
async def content_endpoint(
    request: Request,
    fmt: str,
    status: int = Query(200, description="Status code for the response"),
    delay: int = Query(0, ge=0),
) -> Response:
    settings = request.app.state.settings
    fmt = CONTENT_ALIASES.get(fmt, fmt)
    if fmt not in CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_content_type",
                "message": f"choose from {sorted(CONTENT_TYPES)}",
            },
        )
    media_type, body = CONTENT_TYPES[fmt]
    if fmt == "json":
        body = _json_sample()
    status = require_status(status)
    delay = require_delay(delay, settings.max_delay_ms)
    if delay:
        await guarded_sleep(request.app.state.slow_semaphore, delay)
    if status in NO_BODY_STATUSES:
        return Response(status_code=status)
    return Response(content=body, status_code=status, media_type=media_type)


# ---------------------------------------------------------------- malformed / edge responses


@router.get("/malformed/json", summary="Return invalid JSON with a JSON content type")
async def malformed_json() -> Response:
    return Response(
        content=b'{"this": "is", "not": valid-json',
        status_code=200,
        media_type="application/json",
        headers={"X-HTTP-Lab-Edge": "malformed-json"},
    )


@router.get("/empty", summary="Return an empty 200 response")
async def empty_response() -> Response:
    return Response(content=b"", status_code=200, headers={"X-HTTP-Lab-Edge": "empty"})


@router.get("/truncated", summary="Return a response whose Content-Length exceeds its body")
async def truncated_response() -> Response:
    """Deliberately violates Content-Length framing so clients observe truncation.

    The body is far shorter than the declared Content-Length. Most HTTP clients
    raise an incomplete-read / protocol error, which is the point: testing how
    scrapers and API clients handle truncated responses.
    """

    async def partial() -> AsyncIterator[bytes]:
        yield b"This response is intentionally truncated. "
        yield b"The declared Content-Length is far larger than the body actually sent."

    return StreamingResponse(
        partial(),
        media_type="text/plain; charset=utf-8",
        headers={"content-length": "10000", "X-HTTP-Lab-Edge": "truncated"},
    )


# ---------------------------------------------------------------- response size


@router.get("/response-size/{size}", summary="Return a response body of a given size")
async def response_size_endpoint(
    request: Request, size: str, status: int = Query(200), delay: int = Query(0, ge=0)
) -> Response:
    settings = request.app.state.settings
    try:
        size_bytes = parse_size(size)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_size", "message": str(exc)}
        ) from exc
    if size_bytes > settings.max_response_size_bytes:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "size_exceeds_limit",
                "message": (
                    f"{size} exceeds the configured maximum of "
                    f"{settings.max_response_size_mb}MB "
                    "(raise MAX_RESPONSE_SIZE_MB when self-hosting)"
                ),
            },
        )
    status = require_status(status)
    delay = require_delay(delay, settings.max_delay_ms)
    if delay:
        await guarded_sleep(request.app.state.slow_semaphore, delay)
    if status in NO_BODY_STATUSES:
        return Response(status_code=status)

    async def filler() -> AsyncIterator[bytes]:
        chunk = b"http-lab-filler-0123456789" * 8192  # ~192 KiB per yield
        remaining = size_bytes
        while remaining > 0:
            piece = chunk[:remaining]
            remaining -= len(piece)
            yield piece

    return StreamingResponse(
        filler(),
        media_type="application/octet-stream",
        headers={"Content-Length": str(size_bytes), "X-HTTP-Lab-Size-Bytes": str(size_bytes)},
    )


# ---------------------------------------------------------------- flaky


@dataclass
class FlakyState:
    counter: int = 0
    rng: random.Random | None = None
    touched: float = field(default_factory=time.monotonic)


class FlakyStore:
    """Per-client flaky sequence state, bounded in size and idle lifetime.

    Keys include the client IP, so one client's retries never advance another
    client's sequence. Idle sequences restart after ``idle_ttl`` seconds and the
    least recently used entries are evicted beyond ``max_entries``.
    """

    def __init__(self, max_entries: int = 10_000, idle_ttl: float = 600.0) -> None:
        self._max_entries = max_entries
        self._idle_ttl = idle_ttl
        self._states: OrderedDict[str, FlakyState] = OrderedDict()

    def get(self, key: str) -> FlakyState:
        now = time.monotonic()
        state = self._states.get(key)
        if state is None or now - state.touched > self._idle_ttl:
            state = FlakyState()
            self._states[key] = state
        state.touched = now
        self._states.move_to_end(key)
        while len(self._states) > self._max_entries:
            self._states.popitem(last=False)
        return state

    def __len__(self) -> int:
        return len(self._states)


@router.get("/flaky", summary="Return intermittently failing responses")
async def flaky_endpoint(
    request: Request,
    error_rate: float | None = Query(
        None, ge=0, le=100, description="Percent of requests that fail (0-100)"
    ),
    codes: str | None = Query(
        None, max_length=500, description="Comma-separated status sequence, e.g. 200,200,500"
    ),
    seed: int | None = Query(None, description="Seed for a reproducible failure sequence"),
) -> Response:
    store: FlakyStore = request.app.state.flaky_store
    client = request.state.client_ip

    if codes is not None:
        try:
            sequence = [int(c) for c in codes.split(",") if c.strip()]
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_codes", "message": "codes must be integers"},
            ) from exc
        if not sequence or len(sequence) > 100:
            raise HTTPException(
                status_code=400, detail={"error": "invalid_codes", "message": "provide 1-100 codes"}
            )
        for code in sequence:
            require_status(code)
        state = store.get(f"{client}|codes:{','.join(map(str, sequence))}")
        status = sequence[state.counter % len(sequence)]
        attempt = state.counter + 1
        state.counter += 1
        failed = status >= 400
        if status in NO_BODY_STATUSES:
            return Response(
                status_code=status, headers={"X-Flaky": "true", "X-Flaky-Attempt": str(attempt)}
            )
        body = {"sequence": sequence, "status": status, "attempt": attempt, "flaky": failed}
        return JSONResponse(
            body,
            status_code=status,
            headers={"X-Flaky": "true", "X-Flaky-Attempt": str(attempt)},
        )

    rate = error_rate if error_rate is not None else 30.0
    state = store.get(f"{client}|rate:{rate}:{seed}")
    if state.rng is None:
        state.rng = random.Random(seed) if seed is not None else random.Random()
    attempt = state.counter + 1
    state.counter += 1
    failed = state.rng.random() * 100 < rate
    status = 500 if failed else 200
    body = {"error_rate": rate, "seed": seed, "attempt": attempt, "flaky": failed, "status": status}
    return JSONResponse(
        body,
        status_code=status,
        headers={"X-Flaky": "true", "X-Flaky-Attempt": str(attempt)},
    )


# ---------------------------------------------------------------- response builder

_CONTENT_TYPE_ALLOWLIST = {
    "json",
    "xml",
    "html",
    "text",
    "plain",
    "csv",
    "octet-stream",
    "application/json",
    "application/xml",
    "text/html",
    "text/plain",
    "text/csv",
    "application/octet-stream",
    "application/problem+json",
}
_CONTENT_TYPE_RE = re.compile(r"[A-Za-z0-9!#$%^&*_+.-]+/[A-Za-z0-9!#$%^&*_+.-]+(?:;.*)?")


def _content_type_ok(value: str) -> bool:
    return value in _CONTENT_TYPE_ALLOWLIST or bool(_CONTENT_TYPE_RE.fullmatch(value))


@router.get("/response", summary="Build a fully custom response")
async def response_builder(
    request: Request,
    status: int = Query(200, description="Status code (200-599)"),
    body: str = Query("", max_length=262_144, description="Response body"),
    content_type: str = Query("application/json", max_length=120, description="Content-Type value"),
    delay: int = Query(0, ge=0),
    header: list[str] | None = Query(None, description="Repeatable 'Name: value' response header"),
) -> Response:
    settings = request.app.state.settings
    status = require_status(status)
    delay = require_delay(delay, settings.max_delay_ms)
    extra_headers = parse_http_headers(header)

    size_bytes = len(body.encode("utf-8"))
    if size_bytes > settings.max_response_size_bytes:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "body_too_large",
                "message": (
                    f"body of {size_bytes} bytes exceeds the configured maximum of "
                    f"{settings.max_response_size_mb}MB"
                ),
            },
        )
    # content_type is validated against a strict grammar; the allowlist above is a
    # convenience, custom values matching the grammar are accepted.
    if not _content_type_ok(content_type):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_content_type",
                "message": "content_type must look like a MIME type",
            },
        )

    if delay:
        await guarded_sleep(request.app.state.slow_semaphore, delay)
    if status in NO_BODY_STATUSES:
        return Response(status_code=status, headers=extra_headers)
    if not body:
        return Response(
            content=b"", status_code=status, media_type=content_type, headers=extra_headers
        )
    return Response(
        content=body, status_code=status, media_type=content_type, headers=extra_headers
    )
