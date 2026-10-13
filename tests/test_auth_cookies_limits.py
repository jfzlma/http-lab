from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Callable

import pytest
from app.core.rate_limiter import MemoryRateLimiter, build_rate_limiter, retry_after_seconds
from fastapi.testclient import TestClient


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"authorization": f"Basic {token}"}


def test_basic_auth(client: TestClient) -> None:
    ok = client.get("/basic-auth/u/p", headers=_basic("u", "p"))
    assert ok.json() == {"authenticated": True, "user": "u"}
    bad = client.get("/basic-auth/u/p", headers=_basic("u", "x"))
    assert bad.status_code == 401
    assert bad.headers["www-authenticate"].startswith("Basic")
    assert bad.json()["authenticated"] is False


def test_bearer_auth(client: TestClient) -> None:
    assert client.get("/bearer-auth").status_code == 401
    assert client.get("/bearer-auth", headers={"authorization": "Bearer t"}).json()["token"] == "t"
    wrong = client.get("/bearer-auth?token=x", headers={"authorization": "Bearer t"})
    assert wrong.status_code == 401
    assert wrong.json()["error"] == "unauthorized"


def test_api_key(client: TestClient) -> None:
    assert client.get("/api-key").status_code == 401
    assert client.get("/api-key", headers={"x-api-key": "k"}).json()["api_key"] == "k"
    assert client.get("/api-key?key=z", headers={"x-api-key": "k"}).status_code == 401


def test_set_and_read_cookie(client: TestClient) -> None:
    resp = client.get(
        "/set-cookie",
        params={
            "name": "sid",
            "value": "abc",
            "httponly": True,
            "samesite": "Strict",
            "expires": "Wed, 21 Oct 2099 07:28:00 GMT",
        },
    )
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"]
    assert "sid=abc" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get("/cookies").json() == {"cookies": {"sid": "abc"}}


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"name": "bad name"}, "invalid_cookie_name"),
        ({"name": "a", "value": "x" * 5000}, "cookie_too_long"),
        ({"name": "a", "samesite": "loose"}, "invalid_samesite"),
        ({"name": "a", "path": "nope"}, "invalid_path"),
        ({"name": "a", "expires": "tomorrow"}, "invalid_expires"),
        ({"name": "a", "expires": "Mon, 01 Jan 2001 00:00:00 GMT"}, "invalid_expires"),
    ],
)
def test_set_cookie_validation(client: TestClient, params: dict[str, str], error: str) -> None:
    resp = client.get("/set-cookie", params=params)
    assert resp.status_code == 400
    assert resp.json()["error"] == error


def test_rate_limit_simulator(client: TestClient) -> None:
    codes = [client.get("/rate-limit?requests=2&window=60").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    blocked = client.get("/rate-limit?requests=2&window=60")
    assert blocked.headers["x-ratelimit-remaining"] == "0"
    assert int(blocked.headers["retry-after"]) >= 1
    reset = int(blocked.headers["x-ratelimit-reset"])
    assert time.time() < reset <= time.time() + 61  # epoch seconds, not a monotonic clock
    assert blocked.json()["error"] == "rate_limit_exceeded"


def test_global_rate_limit(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(rate_limit_enabled=True, rate_limit_requests=2, rate_limit_window=60)
    codes = [client.get("/headers").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    # Health probes are exempt, so a strict limit can't make a healthy container "unhealthy".
    assert [client.get(p).status_code for p in ("/healthz", "/readyz", "/healthz")] == [200] * 3
    blocked = client.get("/headers")
    assert "retry-after" in blocked.headers
    assert blocked.json()["request_id"] == blocked.headers["x-request-id"]


def test_global_rate_limit_fails_open(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(rate_limit_enabled=True)

    class Broken:
        async def check(self, *args: object) -> None:
            raise ConnectionError("redis down")

        async def close(self) -> None:
            pass

    client.app.state.rate_limiter = Broken()  # type: ignore[attr-defined]
    assert client.get("/headers").status_code == 200


def test_rate_limit_simulator_reports_backend_outage(client: TestClient) -> None:
    class Broken:
        async def check(self, *args: object) -> None:
            raise ConnectionError("redis down")

        async def close(self) -> None:
            pass

    client.app.state.rate_limiter = Broken()  # type: ignore[attr-defined]
    resp = client.get("/rate-limit")
    assert resp.status_code == 503
    assert resp.json()["error"] == "rate_limiter_unavailable"
    assert resp.headers["retry-after"] == "5"


def test_retry_after_rounding() -> None:
    assert retry_after_seconds(0) == 1
    assert retry_after_seconds(29.2) == 30
    assert retry_after_seconds(30.0) == 30


def test_memory_limiter_evicts_stale_keys() -> None:
    limiter = MemoryRateLimiter(max_keys=2)

    async def scenario() -> None:
        await limiter.check("a", 1, 0.0001)
        await limiter.check("b", 1, 0.0001)
        await asyncio.sleep(0.01)
        state = await limiter.check("c", 1, 0.0001)
        assert state.allowed
        assert set(limiter._windows) == {"c"}
        await limiter.close()

    asyncio.run(scenario())


def test_build_rate_limiter_falls_back_without_redis_extra() -> None:
    assert isinstance(build_rate_limiter(None), MemoryRateLimiter)
    try:
        import redis  # noqa: F401
    except ImportError:
        assert isinstance(build_rate_limiter("redis://localhost:6379/0"), MemoryRateLimiter)


def test_request_id_propagation(client: TestClient) -> None:
    assert (
        client.get("/healthz", headers={"x-request-id": "abc-123"}).headers["x-request-id"]
        == "abc-123"
    )
    minted = client.get("/healthz", headers={"x-request-id": "x" * 500}).headers["x-request-id"]
    assert len(minted) == 32  # oversized inbound id replaced by a fresh uuid4 hex


def test_error_envelope_for_framework_errors(client: TestClient) -> None:
    missing = client.get("/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
    invalid = client.get("/rate-limit?requests=0")
    body = invalid.json()
    assert invalid.status_code == 422
    assert body["error"] == "validation_error"
    assert body["message"].startswith("query.requests")
    assert "request_id" in body


def test_unhandled_errors_return_json(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory()

    @client.app.get("/boom")  # type: ignore[attr-defined, untyped-decorator]
    async def boom() -> None:
        raise RuntimeError("kaboom")

    no_raise = TestClient(client.app, raise_server_exceptions=False)
    resp = no_raise.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["error"] == "internal_error"
    assert "kaboom" not in resp.text
