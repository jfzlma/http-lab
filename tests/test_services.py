from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from app.config import Settings
from app.core.limits import parse_size
from app.logging_conf import redact_headers, redact_query_string, setup_logging
from app.services.ip_intelligence import (
    IpapiProvider,
    IpinfoProvider,
    IPLookupResult,
    LocalProvider,
    TTLBackedProvider,
    build_provider,
)

PUBLIC_IP = "8.8.8.8"


def _mock(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


def test_parse_size_units() -> None:
    assert parse_size("1tb") == 1024**4
    assert parse_size(" 3 MB ") == 3 * 1024 * 1024
    with pytest.raises(ValueError):
        parse_size("1pb")


def test_redaction() -> None:
    headers = redact_headers({"Authorization": "secret", "User-Agent": "a\r\nb"})
    assert headers == {"Authorization": "[REDACTED]", "User-Agent": "a\\r\\nb"}
    assert redact_query_string("token=abc&q=1") == "token=%5BREDACTED%5D&q=1"
    assert redact_query_string("") == ""


def test_setup_logging_is_idempotent() -> None:
    settings = Settings(_env_file=None, log_level="debug")  # type: ignore[call-arg]
    setup_logging(settings)
    assert logging.getLogger().level == logging.DEBUG


def test_settings_validation() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, ip_intelligence_provider="maxmind")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, concurrent_slow_requests=0)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, max_delay_ms=0)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, trusted_proxy_count=-1)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(_env_file=None, log_level="loud")  # type: ignore[call-arg]
    s = Settings(_env_file=None, cors_origins="https://a.example, ,https://b.example")  # type: ignore[call-arg]
    assert s.cors_origin_list == ["https://a.example", "https://b.example"]


def test_local_provider_classifies() -> None:
    result = asyncio.run(LocalProvider().lookup("10.0.0.1"))
    assert result.rfc_classification["is_private"] is True
    assert result.version == 4
    assert "country" in result.unavailable


def test_ipinfo_provider_parses_and_never_sends_private_ips() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/privacy"):
            return httpx.Response(200, json={"vpn": True, "proxy": False, "tor": False})
        return httpx.Response(
            200, json={"country": "US", "city": "Mountain View", "org": "AS15169 Google LLC"}
        )

    async def scenario() -> None:
        provider = IpinfoProvider("tok", 1.0)
        await provider._client.aclose()
        provider._client = _mock(httpx.MockTransport(handler))
        result = await provider.lookup(PUBLIC_IP)
        assert (result.asn, result.organization, result.vpn) == ("AS15169", "Google LLC", True)
        assert result.rfc_classification["is_global"] is True
        private = await provider.lookup("192.168.1.1")
        assert private.lookup_error == "not a public address; external lookup skipped"
        garbage = await provider.lookup("../admin")
        assert garbage.lookup_error == "address is not a valid IP"
        await provider.close()

    asyncio.run(scenario())
    assert seen == [f"/{PUBLIC_IP}/json", f"/{PUBLIC_IP}/privacy"]


def test_ipinfo_provider_handles_upstream_failure() -> None:
    async def scenario() -> None:
        provider = IpinfoProvider("", 1.0)
        await provider._client.aclose()
        provider._client = _mock(httpx.MockTransport(lambda r: httpx.Response(503)))
        result = await provider.lookup(PUBLIC_IP)
        assert result.lookup_error == "ipinfo failed: HTTPStatusError"
        provider._client = _mock(httpx.MockTransport(lambda r: httpx.Response(200, json=[1])))
        result = await provider.lookup(PUBLIC_IP)
        assert result.lookup_error == "ipinfo returned a non-object response"
        await provider.close()

    asyncio.run(scenario())


def test_ipapi_provider() -> None:
    responses = iter(
        [
            httpx.Response(200, json={"country": "US", "asn": 15169, "org": "Google"}),
            httpx.Response(200, json={"error": True, "reason": "RateLimited"}),
            httpx.Response(200, text="not json"),
            httpx.Response(200, json=["x"]),
        ]
    )

    async def scenario() -> None:
        provider = IpapiProvider(1.0)
        await provider._client.aclose()
        provider._client = _mock(httpx.MockTransport(lambda r: next(responses)))
        ok = await provider.lookup(PUBLIC_IP)
        assert (ok.asn, ok.organization, ok.provider) == ("AS15169", "Google", "ipapi")
        assert (await provider.lookup(PUBLIC_IP)).lookup_error == "ipapi failed: RateLimited"
        assert (await provider.lookup(PUBLIC_IP)).lookup_error == "ipapi failed: JSONDecodeError"
        assert (await provider.lookup(PUBLIC_IP)).lookup_error == "ipapi failed: invalid response"
        await provider.close()

    asyncio.run(scenario())


class _Counting(LocalProvider):
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def lookup(self, ip: str) -> IPLookupResult:
        self.calls += 1
        await asyncio.sleep(0.01)
        if self.fail:
            raise RuntimeError("upstream exploded")
        return await super().lookup(ip)


def test_cache_coalesces_concurrent_lookups_and_caches() -> None:
    inner = _Counting()
    cache = TTLBackedProvider(inner, ttl=60)

    async def scenario() -> None:
        results = await asyncio.gather(*(cache.lookup("1.1.1.1") for _ in range(5)))
        assert len({id(r) for r in results}) == 1
        await cache.lookup("1.1.1.1")
        await cache.lookup("2.2.2.2")
        await cache.close()

    asyncio.run(scenario())
    assert inner.calls == 2
    assert cache.name == "local"


def test_cache_propagates_errors_without_caching() -> None:
    inner = _Counting(fail=True)
    cache = TTLBackedProvider(inner, ttl=60)

    async def scenario() -> None:
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cache.lookup("1.1.1.1")

    asyncio.run(scenario())
    assert inner.calls == 2


def test_cache_is_bounded() -> None:
    cache = TTLBackedProvider(LocalProvider(), ttl=60)
    cache._MAX_ENTRIES = 10

    async def scenario() -> None:
        for i in range(25):
            await cache.lookup(f"10.0.0.{i}")

    asyncio.run(scenario())
    assert len(cache._cache) <= 10


def test_build_provider_selects_backend() -> None:
    async def scenario() -> None:
        for name in ("local", "ipinfo", "ipapi"):
            provider = build_provider(name, None, 1.0, 60)
            assert provider.name == name
            await provider.close()

    asyncio.run(scenario())
