# syntax=docker/dockerfile:1
# HTTP Lab container image.
#   docker build -t http-lab .
#   docker run --rm -p 8000:8000 http-lab

# ---- build: resolve dependencies from uv.lock into a self-contained venv ----
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src
COPY pyproject.toml uv.lock ./
# The redis extra lets REDIS_URL share rate-limit state across replicas.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra redis --no-install-project

# ---- runtime: just Python, the venv and the app ----
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="HTTP Lab" \
      org.opencontainers.image.description="Self-hostable HTTP/API testing and inspection playground" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin httplab

COPY --from=build /opt/venv /opt/venv
WORKDIR /srv
COPY app ./app

USER httplab
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('APP_PORT', '8000')}/healthz\", timeout=2)"]

# --no-proxy-headers: client IPs are resolved by TRUSTED_PROXY_COUNT instead.
# exec replaces the shell so uvicorn receives SIGTERM directly for a clean stop.
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"$APP_HOST\" --port \"$APP_PORT\" --no-proxy-headers --no-access-log"]
