"""Cookie testing endpoints. Cookies are never persisted."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["cookies"])

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]{0,200}$")
_SAMESITE = {"lax", "strict", "none"}
_EXPIRES_FMT = "%a, %d %b %Y %H:%M:%S GMT"
MAX_COOKIE_VALUE = 4096


@router.get("/cookies", summary="Return cookies received with the request")
async def cookies_endpoint(request: Request) -> dict[str, Any]:
    return {"cookies": dict(request.cookies.items())}


@router.get("/set-cookie", summary="Set a cookie on the client")
async def set_cookie_endpoint(
    request: Request,
    name: str = Query(..., description="Cookie name"),
    value: str = Query("", description="Cookie value"),
    secure: bool = Query(False, description="Send cookie over HTTPS only"),
    httponly: bool = Query(False, description="Hide cookie from JavaScript"),
    samesite: str = Query("lax", description="SameSite policy: lax, strict, none"),
    max_age: int | None = Query(None, ge=0, le=31_536_000, description="Max-Age in seconds"),
    path: str = Query("/", description="Cookie path"),
    expires: str | None = Query(
        None, description="Expires in RFC 1123 format, e.g. 'Wed, 21 Oct 2026 07:28:00 GMT'"
    ),
) -> JSONResponse:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_cookie_name",
                "message": "name must match [A-Za-z0-9_-]{1,128}",
            },
        )
    if len(value) > MAX_COOKIE_VALUE:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "cookie_too_long",
                "message": f"value exceeds {MAX_COOKIE_VALUE} bytes",
            },
        )
    if samesite.lower() not in _SAMESITE:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_samesite",
                "message": "samesite must be lax, strict, or none",
            },
        )
    if not _PATH_RE.match(path):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_path",
                "message": "path must start with '/' and be <= 200 characters",
            },
        )
    expires_dt: datetime | None = None
    if expires:
        try:
            expires_dt = datetime.strptime(expires, _EXPIRES_FMT).replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_expires",
                    "message": f"expires must match {_EXPIRES_FMT}",
                },
            ) from exc
        if expires_dt <= datetime.now(UTC):
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_expires", "message": "expires must be in the future"},
            )

    same_site = cast(Literal["lax", "strict", "none"], samesite.lower())
    response = JSONResponse(
        {
            "set": {
                "name": name,
                "value": value,
                "secure": secure,
                "httponly": httponly,
                "samesite": same_site,
                "max_age": max_age,
                "path": path,
                "expires": expires,
            }
        }
    )
    response.set_cookie(
        key=name,
        value=value,
        secure=secure,
        httponly=httponly,
        samesite=same_site,
        max_age=max_age,
        path=path,
        expires=expires_dt,
    )
    response.headers["X-Set-Cookie-Sample"] = response.headers.get("set-cookie", "")
    return response
