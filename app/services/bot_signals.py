"""Bot automation heuristics.

This endpoint deliberately does NOT decide whether a client "is a bot". It only
reports *observable signals* and labels them as heuristics. There is no bot
score: each signal is independent and confidence is qualitative.
"""

from __future__ import annotations

from typing import Any

_PYTHON_MARKERS = (
    "python-requests",
    "python/",
    "aiohttp",
    "httpx",
    "urllib",
    "scrapy",
    "python-httpx",
    "requests/",
    "mechanize",
    "pycurl",
)
_CLI_MARKERS = ("curl", "wget", "httpie", "aria2")
_WEBDRIVER_MARKERS = ("webdriver", "selenium", "chromedriver")
_HEADLESS_MARKERS = ("headlesschrome", "phantomjs", "headless")


def detect_bot_signals(
    headers: dict[str, str], http_version: str, tls_available: bool
) -> dict[str, Any]:
    """Produce an ordered list of observable automation signals."""
    ua = (headers.get("user-agent") or "").lower()
    accept = headers.get("accept") or ""

    signals: list[dict[str, Any]] = []

    def add(name: str, detected: bool, confidence: str, evidence: str) -> None:
        signals.append(
            {
                "name": name,
                "detected": detected,
                "confidence": confidence,
                "evidence": evidence,
                "heuristic": True,
            }
        )

    add(
        "python_http_client",
        any(m in ua for m in _PYTHON_MARKERS),
        "high",
        "user-agent matches a Python HTTP library"
        if any(m in ua for m in _PYTHON_MARKERS)
        else "no python library markers in user-agent",
    )
    add(
        "cli_tool",
        any(m in ua for m in _CLI_MARKERS),
        "medium",
        "user-agent matches a CLI tool (not necessarily automated)"
        if any(m in ua for m in _CLI_MARKERS)
        else "no CLI tool markers",
    )
    add(
        "webdriver",
        any(m in ua for m in _WEBDRIVER_MARKERS),
        "high",
        "user-agent mentions a webdriver"
        if any(m in ua for m in _WEBDRIVER_MARKERS)
        else "no webdriver markers in user-agent",
    )
    add(
        "headless_browser",
        any(m in ua for m in _HEADLESS_MARKERS),
        "medium",
        "user-agent mentions a headless browser"
        if any(m in ua for m in _HEADLESS_MARKERS)
        else "no headless markers",
    )
    add(
        "missing_accept_language",
        "accept-language" not in headers,
        "low",
        "real browsers virtually always send Accept-Language",
    )
    add(
        "missing_accept_header",
        "accept" not in headers,
        "low",
        "most HTTP libraries omit Accept, browsers always send it",
    )
    add(
        "missing_fetch_metadata",
        not any(k.startswith("sec-fetch-") for k in headers),
        "low",
        "modern Chromium-based browsers send Sec-Fetch-* metadata",
    )
    add(
        "missing_client_hints",
        "sec-ch-ua" not in headers,
        "low",
        "recent Chrome/Edge send Sec-CH-UA client hints",
    )
    add(
        "json_accept_only",
        bool(accept) and "text/html" not in accept and "application/json" in accept,
        "low",
        "Accept declares JSON but not HTML, typical of API clients",
    )
    add(
        "http2",
        http_version == "2",
        "low",
        "HTTP/2 is typical of modern browsers (also used by headless clients)",
    )
    add("tls_observable", tls_available, "low", "TLS details are visible to the server")

    return {
        "heuristic": True,
        "note": (
            "These are observable heuristics only, not a bot verdict. Any single signal "
            "can be absent or present for legitimate clients (e.g. curl scripts, API "
            "clients, privacy browsers). There is deliberately no bot score."
        ),
        "signals": signals,
    }
