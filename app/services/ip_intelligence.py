"""IP intelligence services.

`IPIntelligenceProvider` is the pluggable abstraction. The default `LocalProvider`
never makes network calls and only reports what can be derived locally (IP
version, RFC classification); everything else is explicitly marked unavailable.
`IpinfoProvider` and `IpapiProvider` are reference implementations for external
providers — enable them through configuration, never hard-code a vendor.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_GEO_FIELDS = ("country", "region", "city", "timezone", "asn", "organization", "isp")
_FLAG_FIELDS = ("datacenter", "proxy", "vpn", "tor")


@dataclass
class IPLookupResult:
    """Normalized IP intelligence result.

    Fields that could not be determined are `None` and listed in `unavailable`.
    """

    ip: str
    version: int
    country: str | None = None
    region: str | None = None
    city: str | None = None
    timezone: str | None = None
    asn: str | None = None
    organization: str | None = None
    isp: str | None = None
    datacenter: bool | None = None
    proxy: bool | None = None
    vpn: bool | None = None
    tor: bool | None = None
    provider: str = "local"
    unavailable: list[str] = field(default_factory=list)
    rfc_classification: dict[str, bool] = field(default_factory=dict)
    lookup_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ip": self.ip,
            "version": self.version,
            "country": self.country,
            "region": self.region,
            "city": self.city,
            "timezone": self.timezone,
            "asn": self.asn,
            "organization": self.organization,
            "isp": self.isp,
            "datacenter": self.datacenter,
            "proxy": self.proxy,
            "vpn": self.vpn,
            "tor": self.tor,
            "provider": self.provider,
            "unavailable": self.unavailable,
            "rfc_classification": self.rfc_classification,
            "lookup_error": self.lookup_error,
        }


class IPIntelligenceProvider(ABC):
    """Pluggable IP intelligence backend."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def lookup(self, ip: str) -> IPLookupResult: ...

    async def close(self) -> None:  # noqa: B027 - optional hook, deliberately not abstract
        """Release network resources (no-op for local providers)."""


def _portable_version(ip: str) -> int:
    return 4 if ":" not in ip else 6


def _rfc_classification(parsed: ipaddress.IPv4Address | ipaddress.IPv6Address) -> dict[str, bool]:
    return {
        "is_private": parsed.is_private,
        "is_global": parsed.is_global,
        "is_loopback": parsed.is_loopback,
        "is_link_local": parsed.is_link_local,
        "is_multicast": parsed.is_multicast,
        "is_reserved": parsed.is_reserved,
    }


def _local_only_result(ip: str, error: str | None = None) -> IPLookupResult:
    return IPLookupResult(
        ip=ip,
        version=_portable_version(ip),
        unavailable=[*_GEO_FIELDS, *_FLAG_FIELDS],
        lookup_error=error,
    )


class LocalProvider(IPIntelligenceProvider):
    """No-network provider: IP version and RFC classification only."""

    @property
    def name(self) -> str:
        return "local"

    async def lookup(self, ip: str) -> IPLookupResult:
        result = _local_only_result(ip)
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            result.lookup_error = "address is not a valid IP"
            return result
        result.rfc_classification = _rfc_classification(parsed)
        return result


class _RemoteProvider(IPIntelligenceProvider):
    """Shared plumbing for HTTP-backed providers.

    Holds one pooled ``httpx.AsyncClient`` for the app's lifetime, and only
    sends *validated, globally routable* addresses upstream: anything else
    (private, loopback, malformed, ...) is answered locally, so internal
    addresses never leak to a third party and the value can never inject a
    path into the provider URL.
    """

    def __init__(self, timeout: float) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)
        self._local = LocalProvider()

    async def lookup(self, ip: str) -> IPLookupResult:
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return await self._local.lookup(ip)
        if not parsed.is_global:
            result = await self._local.lookup(ip)
            result.lookup_error = "not a public address; external lookup skipped"
            return result
        result = await self._remote_lookup(str(parsed))
        result.rfc_classification = _rfc_classification(parsed)
        return result

    @abstractmethod
    async def _remote_lookup(self, ip: str) -> IPLookupResult: ...

    async def close(self) -> None:
        await self._client.aclose()


class IpinfoProvider(_RemoteProvider):
    """https://ipinfo.io provider (requires API token for privacy flags)."""

    @property
    def name(self) -> str:
        return "ipinfo"

    def __init__(self, token: str, timeout: float) -> None:
        super().__init__(timeout)
        self._token = token

    async def _remote_lookup(self, ip: str) -> IPLookupResult:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        unavailable = ["isp", "datacenter"]
        client = self._client
        try:
            response = await client.get(f"https://ipinfo.io/{ip}/json", headers=headers)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("ipinfo lookup failed for %s: %s", ip, exc)
            return _local_only_result(ip, f"ipinfo failed: {type(exc).__name__}")
        if not isinstance(data, dict):
            return _local_only_result(ip, "ipinfo returned a non-object response")
        privacy_raw: Any = {}
        if self._token:
            try:
                privacy = await client.get(f"https://ipinfo.io/{ip}/privacy", headers=headers)
                privacy.raise_for_status()
                privacy_raw = privacy.json()
            except (httpx.HTTPError, ValueError):
                privacy_raw = {}
        if not isinstance(privacy_raw, dict):
            privacy_raw = {}
        org = str(data.get("org") or "")
        asn, _, org_name = org.partition(" ")
        if not self._token:
            unavailable.extend(["proxy", "vpn", "tor"])
        return IPLookupResult(
            ip=ip,
            version=_portable_version(ip),
            country=data.get("country") or None,
            region=data.get("region") or None,
            city=data.get("city") or None,
            timezone=data.get("timezone") or None,
            asn=asn or None,
            organization=org_name or (org or None),
            isp=None,
            datacenter=None,
            proxy=privacy_raw.get("proxy"),
            vpn=privacy_raw.get("vpn"),
            tor=privacy_raw.get("tor"),
            provider=self.name,
            unavailable=unavailable,
            lookup_error=None,
        )


class IpapiProvider(_RemoteProvider):
    """https://ipapi.co provider (free tier, no key required)."""

    @property
    def name(self) -> str:
        return "ipapi"

    async def _remote_lookup(self, ip: str) -> IPLookupResult:
        try:
            data = (await self._client.get(f"https://ipapi.co/{ip}/json/")).json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("ipapi lookup failed for %s: %s", ip, exc)
            return _local_only_result(ip, f"ipapi failed: {type(exc).__name__}")
        if not isinstance(data, dict):
            return _local_only_result(ip, "ipapi failed: invalid response")
        if data.get("error") is not None:
            return _local_only_result(ip, f"ipapi failed: {data.get('reason') or data['error']}")
        asn_value = data.get("asn")
        return IPLookupResult(
            ip=ip,
            version=_portable_version(ip),
            country=data.get("country") or None,
            region=data.get("region") or None,
            city=data.get("city") or None,
            timezone=data.get("timezone") or None,
            asn=f"AS{asn_value}" if asn_value else None,
            organization=data.get("org") or None,
            isp=None,
            datacenter=None,
            proxy=None,
            vpn=None,
            tor=None,
            provider=self.name,
            unavailable=["isp", "datacenter", "proxy", "vpn", "tor"],
            lookup_error=None,
        )


class TTLBackedProvider(IPIntelligenceProvider):
    """Decorator adding a bounded in-memory TTL cache in front of a provider.

    Concurrent lookups of the *same* IP share one upstream call; different IPs
    proceed in parallel. Failed lookups are cached only briefly so a transient
    upstream outage does not stick for the full TTL.
    """

    _MAX_ENTRIES = 5_000
    _ERROR_TTL = 30.0

    def __init__(self, inner: IPIntelligenceProvider, ttl: int) -> None:
        self._inner = inner
        self._ttl = ttl
        self._cache: dict[str, tuple[float, IPLookupResult]] = {}
        self._inflight: dict[str, asyncio.Future[IPLookupResult]] = {}

    @property
    def name(self) -> str:
        return self._inner.name

    async def lookup(self, ip: str) -> IPLookupResult:
        now = time.monotonic()
        cached = self._cache.get(ip)
        if cached and cached[0] > now:
            return cached[1]
        pending = self._inflight.get(ip)
        if pending is not None:
            return await asyncio.shield(pending)

        future: asyncio.Future[IPLookupResult] = asyncio.get_running_loop().create_future()
        self._inflight[ip] = future
        try:
            result = await self._inner.lookup(ip)
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # mark retrieved when nobody else is waiting
            raise
        finally:
            self._inflight.pop(ip, None)
        future.set_result(result)
        ttl = self._ERROR_TTL if result.lookup_error and result.provider == "local" else self._ttl
        self._store(ip, result, now + min(ttl, self._ttl))
        return result

    def _store(self, ip: str, result: IPLookupResult, expires_at: float) -> None:
        if len(self._cache) >= self._MAX_ENTRIES:
            now = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            if len(self._cache) >= self._MAX_ENTRIES:
                # Still full of live entries: drop the oldest insertions.
                for key in list(self._cache)[: self._MAX_ENTRIES // 10]:
                    del self._cache[key]
        self._cache[ip] = (expires_at, result)

    async def close(self) -> None:
        await self._inner.close()


def build_provider(
    provider_name: str, api_key: str | None, timeout: float, cache_ttl: int
) -> IPIntelligenceProvider:
    """Factory matching the configured provider name to an implementation."""
    provider: IPIntelligenceProvider
    if provider_name == "ipinfo":
        provider = IpinfoProvider(api_key or "", timeout)
    elif provider_name == "ipapi":
        provider = IpapiProvider(timeout)
    else:
        provider = LocalProvider()
    return TTLBackedProvider(provider, cache_ttl)
