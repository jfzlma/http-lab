"""Run HTTP Lab with uvicorn: ``python -m app``.

Host and port come from APP_HOST / APP_PORT; auto-reload is enabled only when
APP_ENV=development.
"""

from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_env == "development",
        proxy_headers=False,  # client IP resolution is handled by TRUSTED_PROXY_COUNT
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
