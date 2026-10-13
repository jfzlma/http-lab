from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator

import pytest
from app.core.analytics import Analytics, classify_client, referrer_host
from fastapi.testclient import TestClient

from tests.conftest import make_settings

Events = Callable[[], list[dict[str, object]]]


@pytest.fixture
def events(caplog: pytest.LogCaptureFixture) -> Iterator[Events]:
    def collect() -> list[dict[str, object]]:
        return [json.loads(r.getMessage()) for r in caplog.records if r.name == "httplab.analytics"]

    # Attach the capture handler directly and stop propagation, so each event is
    # captured exactly once whether or not the app has configured this logger yet.
    logger = logging.getLogger("httplab.analytics")
    propagate, level = logger.propagate, logger.level
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    yield collect
    logger.removeHandler(caplog.handler)
    logger.propagate, logger.level = propagate, level


@pytest.mark.parametrize(
    ("ua", "family"),
    [
        (None, "none"),
        ("python-requests/2.32.3", "python-requests"),
        ("Scrapy/2.11 (+https://scrapy.org)", "scrapy"),
        ("python-httpx/0.27.0", "httpx"),
        ("curl/8.7.1", "curl"),
        ("Go-http-client/2.0", "go-http-client"),
        ("PostmanRuntime/7.39.0", "postman"),
        ("Mozilla/5.0 (compatible; Googlebot/2.1)", "search-bot"),
        ("Mozilla/5.0 AppleWebKit/537.36 HeadlessChrome/126.0 Safari/537.36", "headless"),
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/126.0 Safari/537.36 Edg/126.0", "edge"),
        ("Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/126.0 Safari/537.36", "chrome"),
        ("Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15", "safari"),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0", "firefox"),
        ("SomethingCustom/1.0", "other"),
    ],
)
def test_classify_client(ua: str | None, family: str) -> None:
    assert classify_client(ua) == family


def test_referrer_host() -> None:
    assert referrer_host("https://github.com/jfzlma/http-lab", "lab.jafsal.dev") == "github.com"
    assert referrer_host("https://lab.jafsal.dev/#flaky", "lab.jafsal.dev") is None
    assert referrer_host(None, "x") is None
    assert referrer_host("not a url", "x") is None


def test_visitor_id_rotates_daily_and_depends_on_secret() -> None:
    a = Analytics(make_settings(analytics_secret="s1"))
    b = Analytics(make_settings(analytics_secret="s2"))
    assert a.visitor_id("1.2.3.4", "2026-10-13") == a.visitor_id("1.2.3.4", "2026-10-13")
    assert a.visitor_id("1.2.3.4", "2026-10-13") != a.visitor_id("1.2.3.4", "2026-10-14")
    assert a.visitor_id("1.2.3.4", "2026-10-13") != b.visitor_id("1.2.3.4", "2026-10-13")
    assert "1.2.3.4" not in a.visitor_id("1.2.3.4", "2026-10-13")


def test_disabled_by_default_emits_nothing(client: TestClient, events: Events) -> None:
    client.get("/status/418")
    assert events() == []


def test_events_are_aggregate_only(
    client_factory: Callable[..., TestClient], events: Events
) -> None:
    client = client_factory(analytics_enabled=True, analytics_secret="k")
    client.get(
        "/status/503?message=token-abc123",
        headers={
            "user-agent": "python-requests/2.32",
            "referer": "https://github.com/x",
            "authorization": "Bearer secret-token",
            "cookie": "sid=supersecret",
        },
    )
    [event] = events()
    assert event["route"] == "/status/{code}"
    assert event["status"] == 503
    assert event["status_class"] == "5xx"
    assert event["client"] == "python-requests"
    assert event["referrer"] == "github.com"
    assert event["ui"] is False
    assert len(str(event["visitor"])) == 16
    raw = json.dumps(event)
    for leaked in ("token-abc123", "secret-token", "supersecret", "testclient", "/status/503"):
        assert leaked not in raw


def test_infrastructure_paths_are_skipped(
    client_factory: Callable[..., TestClient], events: Events
) -> None:
    client = client_factory(analytics_enabled=True)
    for path in ("/healthz", "/readyz", "/static/style.css"):
        client.get(path)
    client.get("/", headers={"sec-fetch-site": "same-origin"})
    client.get("/nope")
    recorded = [(e["route"], e["ui"]) for e in events()]
    assert recorded == [("/", True), ("unmatched", False)]


def test_rejected_requests_are_recorded(
    client_factory: Callable[..., TestClient], events: Events
) -> None:
    client = client_factory(analytics_enabled=True, max_request_body_mb=1)
    client.post("/anything", content=b"x" * (1024 * 1024 + 1))
    [event] = events()
    assert (event["route"], event["status"]) == ("/anything", 413)


def test_country_only_from_configured_header(
    client_factory: Callable[..., TestClient],
    events: Events,
) -> None:
    client = client_factory(analytics_enabled=True, analytics_country_header="CF-IPCountry")
    client.get("/headers", headers={"cf-ipcountry": "in"})
    client.get("/headers")
    assert [e["country"] for e in events()] == ["IN", None]
