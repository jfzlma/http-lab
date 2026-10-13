"""Static UI pages and assets served from the same origin.

UI responses carry ``Cache-Control: no-cache``: browsers keep their copy but
revalidate it (ETag / Last-Modified, usually a cheap 304) on every load. Without
an explicit policy browsers cache heuristically, and after an upgrade can pair
fresh HTML with a stale app.js, which breaks the page.

Pages also reference assets with a content fingerprint (``/static/app.js?v=<hash>``)
so a changed asset always has a new URL, even for caches filled before this
policy existed.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

router = APIRouter(tags=["ui"], include_in_schema=False)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
UI_CACHE_CONTROL = "no-cache"


class UIStaticFiles(StaticFiles):
    """StaticFiles that always asks the browser to revalidate."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = UI_CACHE_CONTROL
        return response


_ASSET_REF = re.compile(r'(?P<attr>(?:src|href)=")/static/(?P<name>[\w.-]+\.(?:js|css))"')


def _fingerprint(name: str) -> str:
    return hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:12]


def _page(name: str) -> HTMLResponse:
    """Serve an HTML page with its /static asset URLs fingerprinted.

    Read per request so edits show up without a restart; the pages are tiny.
    """
    html = (STATIC_DIR / name).read_text(encoding="utf-8")
    html = _ASSET_REF.sub(
        lambda m: f'{m["attr"]}/static/{m["name"]}?v={_fingerprint(m["name"])}"', html
    )
    return HTMLResponse(html, headers={"Cache-Control": UI_CACHE_CONTROL})


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def index_page() -> HTMLResponse:
    return _page("index.html")


@router.api_route("/fingerprint/browser", methods=["GET", "HEAD"], include_in_schema=False)
async def browser_fingerprint_page() -> HTMLResponse:
    return _page("browser_fingerprint.html")
