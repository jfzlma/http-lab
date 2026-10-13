"""Header / IP / proxy inspection endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..services.ip_intelligence import IPIntelligenceProvider

router = APIRouter(tags=["inspection"])

# Proxy/forwarding related headers inspected by /proxy.
PROXY_HEADERS = (
    "x-forwarded-for",
    "x-real-ip",
    "forwarded",
    "via",
    "cf-connecting-ip",
    "true-client-ip",
    "fastly-client-ip",
    "x-client-ip",
    "x-forwarded-host",
    "x-forwarded-proto",
)


@router.get("/headers", summary="Return all received request headers")
async def headers_endpoint(request: Request) -> dict[str, Any]:
    headers = dict(request.headers.items())
    breakdown = {
        "user_agent": headers.get("user-agent"),
        "accept": headers.get("accept"),
        "accept_language": headers.get("accept-language"),
        "accept_encoding": headers.get("accept-encoding"),
        "referer": headers.get("referer"),
        "origin": headers.get("origin"),
        "host": headers.get("host"),
        "content_type": headers.get("content-type"),
        "content_length": headers.get("content-length"),
        "sec_fetch": {k: v for k, v in headers.items() if k.startswith("sec-fetch-")},
        "client_hints": {k: v for k, v in headers.items() if k.startswith("sec-ch-")},
        "forwarding_headers": {k: v for k, v in headers.items() if k in PROXY_HEADERS},
    }
    return {
        "headers": headers,
        "breakdown": breakdown,
        "note": "A missing header does not imply automation or a bot. Headers are returned "
        "exactly as received.",
    }


@router.get("/ip", summary="Report the client IP and best-effort IP intelligence")
async def ip_endpoint(request: Request) -> dict[str, Any]:
    provider: IPIntelligenceProvider = request.app.state.ip_provider
    result = await provider.lookup(request.state.client_ip)
    data = result.as_dict()
    data["note"] = (
        "Determined with provider: "
        + provider.name
        + ". Fields that could not be resolved are null and listed under 'unavailable'."
    )
    return data


@router.get("/proxy", summary="Inspect proxy-related headers and signals")
async def proxy_endpoint(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    headers = dict(request.headers.items())
    observed = {k: headers[k] for k in PROXY_HEADERS if k in headers}

    forwarded_for: list[str] = []
    xff = headers.get("x-forwarded-for")
    if xff:
        forwarded_for = [p.strip() for p in xff.split(",") if p.strip()]
    forwarded_hosts: list[str] = []
    forwarded = headers.get("forwarded")
    if forwarded:
        for element in forwarded.split(","):
            for part in element.split(";"):
                key, _, value = part.strip().partition("=")
                if key.strip().lower() == "for":
                    forwarded_hosts.append(value.strip().strip('"'))

    socket_ip = request.client.host if request.client else None
    source = "socket"
    if settings.trusted_proxy_count > 0 and xff:
        source = "x-forwarded-for"

    provider: IPIntelligenceProvider = request.app.state.ip_provider
    lookup = await provider.lookup(request.state.client_ip)
    provider_flags: dict[str, Any] = {}
    if any(getattr(lookup, f) is not None for f in ("proxy", "vpn", "tor", "datacenter")):
        provider_flags = {
            "datacenter": lookup.datacenter,
            "proxy": lookup.proxy,
            "vpn": lookup.vpn,
            "tor": lookup.tor,
            "provider": lookup.provider,
        }

    proxy_detected = (
        bool(observed)
        or bool(forwarded_hosts)
        or any(provider_flags.get(k) for k in ("proxy", "vpn", "tor"))
    )

    return {
        "client_ip": request.state.client_ip,
        "client_ip_source": source,
        "forwarded_for": forwarded_for,
        "forwarded_hosts": forwarded_hosts,
        "proxy_headers": observed,
        "inferred": {
            "forwarding_headers_present": bool(observed) or bool(forwarded_hosts),
            "xff_first_matches_socket": bool(forwarded_for and forwarded_for[0] == socket_ip),
        },
        "provider": provider_flags,
        "proxy_detected": proxy_detected,
        "trust_model": (
            "direct connection (TRUSTED_PROXY_COUNT=0; forwarding headers are treated as "
            "client-supplied, not authoritative)"
            if settings.trusted_proxy_count == 0
            else f"{settings.trusted_proxy_count} trusted reverse proxies configured"
        ),
        "note": (
            "Proxy detection is not perfect. 'proxy_headers' and 'forwarded_for' are "
            "observed data; 'provider' fields are third-party inferences; the verdict is "
            "'proxy_detected' only when strong evidence exists."
        ),
    }
