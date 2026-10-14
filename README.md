# HTTP Lab

A self-hostable HTTP/API testing and inspection playground, in the spirit of
httpbin. It shows exactly what a server receives from your client, and it can
simulate the server behaviour that clients, scrapers and retry logic need to
handle.

- **Inspect:** `/anything`, `/headers`, `/ip`, `/proxy`, `/fingerprint`, `/bot`,
  and the in-browser `/fingerprint/browser` page
- **Simulate:** `/status/{code}`, `/delay/{ms}`, `/delay/random`, `/redirect/{n}`,
  `/content/{fmt}`, `/malformed/json`, `/empty`, `/truncated`,
  `/response-size/{size}`, `/flaky`, `/rate-limit`, and the `/response` builder
- **Client testing:** `/basic-auth/{user}/{pass}`, `/bearer-auth`, `/api-key`,
  `/cookies`, `/set-cookie`
- **Ops:** `/healthz` and `/readyz`

The web UI is at `/`, and the interactive API reference is at `/docs`.

## Quick start

Requires Python 3.12+. With [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
uv run python -m app            # http://127.0.0.1:8000 (reload when APP_ENV=development)
```

With pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # or requirements-dev.txt for tests and linters
python -m app
```

### Docker

```bash
docker build -t http-lab .
docker run --rm -p 8000:8000 http-lab
```

The image runs as a non-root user, works with a read-only filesystem
(`--read-only --cap-drop ALL`) and has a built-in `HEALTHCHECK` on `/healthz`.

### Docker Compose (with Redis)

```bash
docker compose up --build                       # http://127.0.0.1:8000
HTTP_LAB_PORT=9000 docker compose up --build    # pick another host port
```

Compose runs Redis alongside the app, so rate-limit state is shared across
replicas. It reads `.env` if present. The port is bound to `127.0.0.1` by
default; remove that prefix in `docker-compose.yml` to expose it on your network.

## Configuration

Copy `.env.example` to `.env` and adjust the values. The settings that matter most:

| Variable | Default | Purpose |
|---|---|---|
| `TRUSTED_PROXY_COUNT` | `0` | How many reverse proxies to trust in `X-Forwarded-For`. At `0`, forwarding headers are treated as client-supplied data. |
| `RATE_LIMIT_ENABLED` | `false` | Global per-IP cap. Turn it on for public deployments. |
| `REDIS_URL` | empty | Shares rate-limit state across workers (needs the `redis` extra). If Redis is unreachable, requests are allowed rather than failed. |
| `MAX_DELAY_MS` / `MAX_RESPONSE_SIZE_MB` / `MAX_REQUEST_BODY_MB` | 30000 / 10 / 1 | Safety limits for the simulators. |
| `CONCURRENT_SLOW_REQUESTS` | `50` | Number of delayed requests allowed at once. Once it is reached, new ones get `503` with `Retry-After`. |
| `IP_INTELLIGENCE_PROVIDER` | `local` | One of `local` (no network calls), `ipinfo` or `ipapi`. Only public IPs are ever sent to an external provider. |

Behind a reverse proxy, set `TRUSTED_PROXY_COUNT` and do **not** turn on
uvicorn's `--proxy-headers`. Client IP resolution is done in one place only.

## Errors

Errors produced by HTTP Lab itself (validation, safety limits, rate limits,
unexpected failures) share one JSON envelope:

```json
{"error": "delay_exceeds_limit", "message": "delay 99999ms exceeds configured maximum 30000ms", "request_id": "…"}
```

Simulated statuses (`/status/500`, `/flaky` and so on) are ordinary responses
and are not wrapped. Every response carries `X-Request-Id`. A well-formed
inbound `X-Request-Id` (up to 128 characters from `[A-Za-z0-9._:-]`) is
reused; any other value is replaced with a new id.

## Privacy

Request bodies are never stored or logged. `Authorization`, `Cookie` and
API-key headers, along with sensitive query parameters, are redacted from logs.
`LOG_REQUESTS=false` turns request logging off entirely.

Optional usage analytics (`ANALYTICS_ENABLED=true`) record aggregate events
only: route template, client family, status, latency and a visitor hash that
rotates daily. No IPs, paths, bodies or credentials are stored. See
[docs/analytics.md](docs/analytics.md) for what is recorded and ready-made
queries for Cloud Run.

## Development

```bash
uv run pytest          # tests + coverage (must stay ≥ 85%)
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

CI (`.github/workflows/ci.yml`) runs the same checks on Python 3.12 and 3.13,
then builds the Docker image and smoke-tests the container.

`uv.lock` is the source of truth for dependencies. After changing them, regenerate
the pip files (CI fails if they are stale):

```bash
uv lock
uv export --frozen --no-dev --extra redis --no-hashes --no-emit-project -o requirements.txt
uv export --frozen --extra dev --extra redis --no-hashes --no-emit-project -o requirements-dev.txt
```

## Layout

```
app/
  main.py            app factory: wiring, middleware, lifespan
  config.py          pydantic-settings configuration
  api/               one router per feature area
  core/              middleware, error envelope, limits, rate limiting
  services/          fingerprinting, bot heuristics, IP intelligence
  static/            dependency-free web UI
tests/               pytest suite (FastAPI TestClient)
```
