"""Application factory and ASGI entrypoint."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from . import __version__
from .api import (
    auth,
    bot,
    cookies,
    echo,
    fingerprint,
    inspect,
    pages,
    rate_limit,
    simulators,
    status,
)
from .config import Settings, get_settings
from .core.errors import install_error_handlers
from .core.rate_limiter import build_rate_limiter
from .core.security import RequestContextMiddleware
from .logging_conf import setup_logging
from .services.ip_intelligence import build_provider

STATIC_DIR = Path(__file__).resolve().parent / "static"

DESCRIPTION = """
HTTP Lab is a self-hostable HTTP/API testing and inspection playground.

* **Inspect** exactly what a server receives: `/anything`, `/headers`, `/ip`,
  `/proxy`, `/fingerprint`, `/bot` and the browser-side `/fingerprint/browser`.
* **Simulate** server behaviour: `/status/{code}`, `/delay/{ms}`, `/redirect/{n}`,
  `/content/{fmt}`, `/malformed/*`, `/response-size/{size}`, `/flaky`, `/rate-limit`
  and the generic `/response` builder.
* **Test clients**: auth endpoints (`/basic-auth`, `/bearer-auth`, `/api-key`),
  cookie handling, retries, timeouts, redirects and error parsing.

Privacy: request bodies are never stored; Authorization/Cookie/API-key headers are
redacted from logs; `LOG_REQUESTS=false` disables request logging entirely.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application (testable with explicit settings)."""
    settings = settings or get_settings()
    setup_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.rate_limiter.close()
        await app.state.ip_provider.close()

    app = FastAPI(
        title="HTTP Lab",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.state.settings = settings
    app.state.rate_limiter = build_rate_limiter(settings.redis_url)
    app.state.slow_semaphore = asyncio.Semaphore(settings.concurrent_slow_requests)
    app.state.ip_provider = build_provider(
        settings.ip_intelligence_provider,
        settings.ip_intelligence_api_key,
        settings.ip_info_timeout,
        settings.ip_info_cache_ttl,
    )
    app.state.flaky_store = simulators.FlakyStore()

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestContextMiddleware, settings=settings)
    install_error_handlers(app)

    routers = [
        status.router,
        echo.router,
        inspect.router,
        fingerprint.router,
        bot.router,
        simulators.router,
        auth.router,
        cookies.router,
        rate_limit.router,
        pages.router,
    ]
    for router in routers:
        app.include_router(router)
    # Route templates for analytics on requests rejected before routing (413/429).
    app.state.route_table = [
        (route.path_regex, route.path)
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute)
    ]

    app.mount("/static", pages.UIStaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/healthz", tags=["ops"], summary="Liveness probe")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["ops"], summary="Readiness probe")
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    return app


app = create_app()
