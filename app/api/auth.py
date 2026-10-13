"""Authentication testing endpoints — purely for testing HTTP clients.

Credentials are validated in-memory and never stored, logged, or persisted.
Response bodies may echo the credential *back to the same requester* (as
httpbin does); logs and storage never contain them.
"""

from __future__ import annotations

import base64
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["auth"])


def _bearer_value(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()


@router.get("/basic-auth/{username}/{password}", summary="Require HTTP Basic authentication")
async def basic_auth(username: str, password: str, request: Request) -> dict[str, Any]:
    expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    got = request.headers.get("authorization", "")
    if not secrets.compare_digest(got, expected):
        raise HTTPException(
            status_code=401,
            detail={"authenticated": False, "user": username},
            headers={"WWW-Authenticate": 'Basic realm="http-lab"'},
        )
    return {"authenticated": True, "user": username}


@router.get("/bearer-auth", summary="Require a Bearer token")
async def bearer_auth(
    request: Request,
    token: str | None = Query(
        None, max_length=512, description="Expected token; omit to accept any bearer token"
    ),
) -> dict[str, Any]:
    value = _bearer_value(request)
    if value is None:
        raise HTTPException(
            status_code=401,
            detail={"authenticated": False, "detail": "missing bearer token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if token is not None and not secrets.compare_digest(value, token):
        raise HTTPException(
            status_code=401, detail={"authenticated": False, "detail": "invalid bearer token"}
        )
    return {"authenticated": True, "token": value}


@router.get("/api-key", summary="Require an API key header")
async def api_key(
    request: Request,
    key: str | None = Query(
        None, max_length=512, description="Expected key; omit to accept any X-API-Key"
    ),
) -> dict[str, Any]:
    provided = request.headers.get("x-api-key") or request.headers.get("x-api-token")
    if not provided:
        raise HTTPException(
            status_code=401,
            detail={"authenticated": False, "detail": "missing X-API-Key header"},
        )
    if key is not None and not secrets.compare_digest(provided, key):
        raise HTTPException(
            status_code=401, detail={"authenticated": False, "detail": "invalid api key"}
        )
    return {"authenticated": True, "api_key": provided}
