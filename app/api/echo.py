"""Request echo / inspection endpoint: /anything."""

from __future__ import annotations

import base64
import json
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..services.fingerprint import http_version, tls_analysis

router = APIRouter(tags=["inspection"])


async def read_body_limited(request: Request, limit: int) -> bytes:
    """Read the raw request body, rejecting payloads over *limit* bytes."""
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "payload_too_large",
                    "message": f"request body exceeds {limit} bytes",
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _collapse(values: dict[str, list[str]]) -> dict[str, Any]:
    return {k: (v[0] if len(v) == 1 else v) for k, v in values.items()}


async def build_echo(request: Request, raw_body: bytes) -> dict[str, Any]:
    """Assemble the /anything payload preserving request fidelity."""
    headers = dict(request.headers.items())
    header_list = [[name, value] for name, value in request.headers.items()]
    cookies = dict(request.cookies.items())

    query_string = request.url.query
    query = (
        _collapse(urllib.parse.parse_qs(query_string, keep_blank_values=True))
        if query_string
        else {}
    )

    scheme = request.scope.get("scheme", "http")
    host = request.headers.get("host") or (request.scope.get("server") or ["localhost"])[0]
    url = f"{scheme}://{host}{request.url.path}"
    if query_string:
        url += f"?{query_string}"

    content_type = headers.get("content-type", "")
    body_text: str | None = None
    body_base64: str | None = None
    body_json: Any | None = None
    form: dict[str, Any] | None = None

    if raw_body:
        try:
            body_text = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            body_base64 = base64.b64encode(raw_body).decode("ascii")
        if body_text is not None:
            if content_type.startswith("application/json"):
                try:
                    body_json = json.loads(body_text)
                except json.JSONDecodeError:
                    body_json = None
            elif content_type.startswith("application/x-www-form-urlencoded"):
                form = _collapse(urllib.parse.parse_qs(body_text, keep_blank_values=True))

    client = request.client
    payload: dict[str, Any] = {
        "method": request.method,
        "url": url,
        "path": request.url.path,
        "raw_query": query_string or None,
        "query": query or None,
        "headers": headers,
        "header_list": header_list,
        "cookies": cookies,
        "body": body_text,
        "body_base64": body_base64,
        "json": body_json,
        "form": form,
        "client": {"ip": client.host if client else None, "port": client.port if client else None},
        "protocol": f"HTTP/{http_version(request)}",
        "tls": tls_analysis(request),
        "request_id": request.state.request_id,
    }
    # Omit keys the schema marks as optional when they carry no data.
    for key in ("raw_query", "body", "body_base64", "json", "form"):
        if payload[key] is None:
            del payload[key]
    return payload


@router.api_route(
    "/anything",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    summary="Echo the full incoming request",
    description=(
        "Return everything the server received: method, URL, query, headers, cookies, "
        "body, client address, protocol and TLS metadata. The body is limited by "
        "MAX_REQUEST_BODY_MB; larger payloads are rejected with 413."
    ),
)
async def anything(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    raw = await read_body_limited(request, settings.max_request_body_bytes)
    return JSONResponse(await build_echo(request, raw))
