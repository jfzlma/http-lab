"""Server-side HTTP request fingerprint analysis.

Only *observed* characteristics are reported. TLS metadata is only available
when the deployment terminates TLS itself (uvicorn exposes it in the ASGI
scope); behind a reverse proxy it degrades gracefully. JA3/JA4 values are never
fabricated — they require a TLS-proxy or packet capture and are reported as
`None` with an explanatory note.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request


def normalized_headers(request: Request) -> dict[str, str]:
    return {name.lower(): value for name, value in request.headers.items()}


def http_version(request: Request) -> str:
    """The negotiated HTTP version, e.g. ``"1.1"`` or ``"2"``."""
    return str(request.scope.get("http_version", "1.1"))


def http_analysis(request: Request) -> dict[str, Any]:
    scope = request.scope
    return {
        "version": f"HTTP/{http_version(request)}",
        "method": request.method,
        "scheme": scope.get("scheme", "http"),
        "host": request.headers.get("host") or (scope.get("server") or ["unknown"])[0],
        "path": request.url.path,
        "query_string": request.url.query or None,
    }


def tls_analysis(request: Request) -> dict[str, Any]:
    tls = request.scope.get("tls")
    if not tls or not isinstance(tls, dict):
        return {
            "available": False,
            "version": None,
            "cipher": None,
            "ja3": None,
            "ja4": None,
            "note": (
                "TLS metadata is not exposed by this deployment (plain HTTP, or TLS "
                "is terminated by a reverse proxy)."
            ),
        }
    version = tls.get("version")
    cipher = tls.get("cipher_name") or tls.get("cipher")
    if isinstance(cipher, tuple):
        cipher = cipher[0]
    return {
        "available": True,
        "version": version if isinstance(version, str) else None,
        "cipher": cipher if isinstance(cipher, str) else None,
        "ja3": None,
        "ja4": None,
        "note": "JA3/JA4 require a TLS-proxy or packet capture; not provided by this deployment.",
    }


def _accepted_encodings(value: str) -> set[str]:
    """Codings listed in Accept-Encoding, ignoring those disabled with q=0."""
    codings: set[str] = set()
    for item in value.split(","):
        coding, *params = (part.strip().lower() for part in item.split(";"))
        if not coding:
            continue
        if any(_is_zero_qvalue(p) for p in params):
            continue
        codings.add(coding)
    return codings


def _is_zero_qvalue(param: str) -> bool:
    name, _, value = param.replace(" ", "").partition("=")
    if name != "q":
        return False
    try:
        return float(value) == 0
    except ValueError:
        return False


def header_analysis(headers: dict[str, str]) -> dict[str, Any]:
    accept = headers.get("accept", "")
    encodings = _accepted_encodings(headers.get("accept-encoding", ""))
    return {
        "user_agent": headers.get("user-agent"),
        "accept": accept or None,
        "accept_language": headers.get("accept-language"),
        "accept_encoding": headers.get("accept-encoding"),
        "referer": headers.get("referer"),
        "origin": headers.get("origin"),
        "host": headers.get("host"),
        "content_type": headers.get("content-type"),
        "lang_charset": headers.get("accept-charset"),
        "dnt": headers.get("dnt"),
        "compression": {
            coding: coding in encodings or "*" in encodings
            for coding in ("gzip", "deflate", "br", "zstd")
        },
        "fetch_metadata": {k: v for k, v in headers.items() if k.startswith("sec-fetch-")},
        "client_hints": {k: v for k, v in headers.items() if k.startswith("sec-ch-")},
    }


def analyze_request(request: Request) -> dict[str, Any]:
    """Assemble the full /fingerprint payload from an incoming request."""
    headers = normalized_headers(request)
    signals: list[str] = []
    if http_version(request) == "2":
        signals.append("http2")
    if request.scope.get("tls"):
        signals.append("tls_observable")
    if "sec-ch-ua" in headers:
        signals.append("client_hints_present")
    if any(k.startswith("sec-fetch-") for k in headers):
        signals.append("fetch_metadata_present")
    return {
        "http": http_analysis(request),
        "tls": tls_analysis(request),
        "headers": header_analysis(headers),
        "all_headers": headers,
        "signals": signals,
    }
