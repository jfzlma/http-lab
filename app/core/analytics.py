"""Privacy-preserving usage analytics.

When ``ANALYTICS_ENABLED`` is on, every request produces one JSON line on stdout
(logger ``httplab.analytics``). Cloud Run / Cloud Logging turn each line into a
structured ``jsonPayload`` that can be queried with Log Analytics (SQL); see
``docs/analytics.md``.

What an event contains, and deliberately does not:

* ``route``: the route *template* (``/status/{code}``), never the raw path or
  query string, which can carry tokens or personal data.
* ``client``: a coarse family derived from the User-Agent (``python-requests``,
  ``curl``, ``chrome``...); the full User-Agent string is not stored.
* ``visitor``: a keyed hash of the client IP that rotates every UTC day. It
  counts distinct users per day but cannot be linked across days or reversed
  to an IP without the secret.
* ``referrer``: the referring *host* only (e.g. ``github.com``).
* ``country``: only when a trusted upstream header provides it
  (``ANALYTICS_COUNTRY_HEADER``); no IP geolocation lookups are made.
* No IPs, bodies, cookies, credentials or other headers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import sys
import urllib.parse
from datetime import UTC, datetime

from fastapi import Request

from ..config import Settings

logger = logging.getLogger("httplab.analytics")

# Paths that are infrastructure noise rather than usage.
_SKIP_PREFIXES = ("/static/",)
_SKIP_PATHS = frozenset({"/healthz", "/readyz", "/favicon.ico", "/robots.txt"})

# Ordered: the first matching pattern wins, so specific tools come before the
# generic browser engines they often embed (Edge contains "chrome", Chrome
# contains "safari", HeadlessChrome contains "chrome", Scrapy contains "python").
_CLIENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("scrapy", r"scrapy"),
        ("python-requests", r"python-requests"),
        ("httpx", r"python-httpx|\bhttpx\b"),
        ("aiohttp", r"aiohttp"),
        ("python-urllib", r"python-urllib|\burllib"),
        ("curl", r"^curl/|\bcurl\b"),
        ("wget", r"^wget|\bwget\b"),
        ("httpie", r"httpie"),
        ("postman", r"postmanruntime"),
        ("insomnia", r"insomnia"),
        ("go-http-client", r"go-http-client"),
        ("node", r"\bnode-fetch\b|\bundici\b|\baxios\b|\bnode\b|\bgot\b"),
        ("okhttp", r"okhttp"),
        ("java", r"\bjava/|apache-httpclient"),
        ("powershell", r"powershell"),
        ("search-bot", r"googlebot|bingbot|duckduckbot|yandexbot|baiduspider|applebot"),
        ("ai-crawler", r"gptbot|claudebot|anthropic-ai|ccbot|perplexitybot|bytespider"),
        ("monitoring", r"uptimerobot|pingdom|statuscake|better ?uptime|health-?check"),
        ("headless", r"headlesschrome|phantomjs|puppeteer|playwright|selenium|webdriver"),
        ("bot-other", r"bot\b|crawler|spider|scraper"),
        ("edge", r"\bedg(e|a|ios)?/"),
        ("opera", r"\bopr/|opera"),
        ("firefox", r"firefox/|fxios/"),
        ("chrome", r"chrome/|crios/"),
        ("safari", r"safari/"),
    )
)


def classify_client(user_agent: str | None) -> str:
    """Map a User-Agent to a coarse client family.

    Returns "none" when the header is absent and "other" when it is unrecognised.
    """
    if not user_agent or not user_agent.strip():
        return "none"
    for name, pattern in _CLIENT_PATTERNS:
        if pattern.search(user_agent):
            return name
    return "other"


def route_template(request: Request) -> str:
    """The matched route's path template, e.g. ``/status/{code}``.

    After routing, the router has stored the matched route in the scope. Requests
    rejected earlier by the guard middleware (413/429) never reach the router, so
    they are matched against the route table built in ``create_app``.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    table = getattr(request.app.state, "route_table", ())
    for regex, template in table:
        if regex.match(request.url.path):
            return str(template)
    return "unmatched"


def referrer_host(referer: str | None, own_host: str | None) -> str | None:
    """Host part of the Referer, or None for same-site/absent/invalid values."""
    if not referer:
        return None
    try:
        host = urllib.parse.urlsplit(referer).hostname
    except ValueError:
        return None
    if not host:
        return None
    own = (own_host or "").split(":")[0].lower()
    return None if host.lower() == own else host.lower()


class Analytics:
    """Builds and emits analytics events (see module docstring)."""

    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.analytics_enabled
        self._country_header = (settings.analytics_country_header or "").lower() or None
        secret = settings.analytics_secret
        if self.enabled and not secret:
            # Still private, but visitor ids then also change on every restart.
            logging.getLogger(__name__).warning(
                "ANALYTICS_SECRET is not set; using a random per-process secret, so "
                "daily visitor counts reset when the service restarts"
            )
        self._secret = (secret or secrets.token_hex(32)).encode()
        if self.enabled:
            _configure_logger()

    def visitor_id(self, client_ip: str, day: str) -> str:
        digest = hmac.new(self._secret, f"{day}|{client_ip}".encode(), hashlib.sha256)
        return digest.hexdigest()[:16]

    def should_record(self, path: str) -> bool:
        return self.enabled and path not in _SKIP_PATHS and not path.startswith(_SKIP_PREFIXES)

    def event(self, request: Request, status: int, duration_ms: float) -> dict[str, object]:
        now = datetime.now(UTC)
        headers = request.headers
        country = headers.get(self._country_header) if self._country_header else None
        return {
            "event": "http_request",
            "severity": "INFO",
            "ts": now.isoformat(timespec="seconds"),
            "route": route_template(request),
            "method": request.method,
            "status": status,
            "status_class": f"{status // 100}xx",
            "duration_ms": round(duration_ms, 1),
            "client": classify_client(headers.get("user-agent")),
            "visitor": self.visitor_id(request.state.client_ip, now.date().isoformat()),
            "referrer": referrer_host(headers.get("referer"), headers.get("host")),
            "country": country[:2].upper() if country else None,
            # True for the web UI itself and the fetches it makes from the page.
            "ui": request.url.path == "/" or headers.get("sec-fetch-site") == "same-origin",
        }

    def record(self, request: Request, status: int, duration_ms: float) -> None:
        if not self.should_record(request.url.path):
            return
        try:
            logger.info(json.dumps(self.event(request, status, duration_ms), separators=(",", ":")))
        except Exception:  # analytics must never break a request
            logging.getLogger(__name__).debug("analytics event failed", exc_info=True)


def _configure_logger() -> None:
    """Emit bare JSON lines (no timestamp prefix) so Cloud Logging parses them."""
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
