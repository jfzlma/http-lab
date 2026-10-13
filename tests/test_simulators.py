from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from app.api.simulators import FlakyStore
from fastapi.testclient import TestClient


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_status_any_method(client: TestClient, method: str) -> None:
    resp = getattr(client, method)("/status/418?message=teapot")
    assert resp.status_code == 418
    assert resp.json()["message"] == "teapot"


def test_status_body_and_headers(client: TestClient) -> None:
    resp = client.get(
        "/status/503",
        params={"body": '{"down": true}', "header": "Cache-Control: max-age=0, no-cache"},
    )
    assert resp.status_code == 503
    assert resp.json() == {"down": True}
    assert resp.headers["cache-control"] == "max-age=0, no-cache"
    text = client.get("/status/200", params={"body": "plain"})
    assert text.headers["content-type"].startswith("text/plain")
    assert client.get("/status/204").content == b""


@pytest.mark.parametrize(
    ("header", "error"),
    [
        ("NoColon", "invalid_header"),
        ("Content-Length: 5", "forbidden_header"),
        ("X-Bad: a\x01b", "invalid_header"),
    ],
)
def test_status_rejects_bad_headers(client: TestClient, header: str, error: str) -> None:
    resp = client.get("/status/200", params={"header": header})
    assert resp.status_code == 400
    assert resp.json()["error"] == error


def test_status_out_of_range(client: TestClient) -> None:
    resp = client.get("/status/999")
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_status"


def test_delay_fixed_and_random(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(max_delay_ms=50)
    assert client.get("/delay/10").json()["slept_ms"] == 10
    rand = client.get("/delay/random?min=1&max=5").json()
    assert 1 <= rand["slept_ms"] <= 5
    assert client.get("/delay/51").json()["error"] == "delay_exceeds_limit"
    assert client.get("/delay/-1").json()["error"] == "invalid_delay"
    assert client.get("/delay/random?min=5&max=1").json()["error"] == "invalid_range"


def test_slow_requests_are_shed_when_saturated(client: TestClient) -> None:
    sem: asyncio.Semaphore = client.app.state.slow_semaphore  # type: ignore[attr-defined]
    sem._value = 0  # simulate every slot taken
    resp = client.get("/delay/1")
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "1"
    assert resp.json()["error"] == "too_many_slow_requests"


def test_redirect_chain(client: TestClient) -> None:
    first = client.get("/redirect/2?status=307", follow_redirects=False)
    assert first.status_code == 307
    assert first.headers["location"] == "/redirect/1?status=307&to=%2Fanything"
    final = client.get("/redirect/3")
    assert final.status_code == 200
    assert len(final.history) == 3
    assert final.json()["path"] == "/anything"
    assert client.get("/redirect/0").json()["redirected"] is False


@pytest.mark.parametrize(
    ("url", "error"),
    [
        ("/redirect/99", "redirect_chain_too_long"),
        ("/redirect/1?status=200", "unsupported_status"),
        ("/redirect/1?to=//evil.example", "unsafe_target"),
        ("/redirect/1?to=https://evil.example", "unsafe_target"),
    ],
)
def test_redirect_validation(client: TestClient, url: str, error: str) -> None:
    resp = client.get(url, follow_redirects=False)
    assert resp.status_code == 400
    assert resp.json()["error"] == error


@pytest.mark.parametrize(
    ("fmt", "media"),
    [
        ("json", "application/json"),
        ("xml", "application/xml"),
        ("html", "text/html"),
        ("plain", "text/plain"),
        ("csv", "text/csv"),
        ("octet-stream", "application/octet-stream"),
        ("problem-json", "application/problem+json"),
    ],
)
def test_content_types(client: TestClient, fmt: str, media: str) -> None:
    resp = client.get(f"/content/{fmt}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(media)


def test_content_edge_cases(client: TestClient) -> None:
    assert client.get("/content/yaml").json()["error"] == "unsupported_content_type"
    assert client.get("/content/json?status=304").content == b""
    assert client.get("/content/text?delay=1").status_code == 200


def test_malformed_and_empty(client: TestClient) -> None:
    bad = client.get("/malformed/json")
    assert bad.headers["content-type"] == "application/json"
    with pytest.raises(ValueError):
        bad.json()
    assert client.get("/empty").content == b""


@pytest.mark.parametrize(("size", "expected"), [("512", 512), ("2kb", 2048), ("1K", 1024)])
def test_response_size(client: TestClient, size: str, expected: int) -> None:
    resp = client.get(f"/response-size/{size}")
    assert len(resp.content) == expected
    assert resp.headers["x-http-lab-size-bytes"] == str(expected)


def test_response_size_limits(client: TestClient) -> None:
    assert client.get("/response-size/1tb").json()["error"] == "size_exceeds_limit"
    assert client.get("/response-size/lots").json()["error"] == "invalid_size"
    assert client.get("/response-size/10?status=204").content == b""
    assert len(client.get("/response-size/10?delay=1").content) == 10


def test_flaky_sequence_is_per_client(client: TestClient) -> None:
    statuses = [client.get("/flaky?codes=200,500").status_code for _ in range(3)]
    assert statuses == [200, 500, 200]
    other = client.get(
        "/flaky?codes=200,500", headers={"x-forwarded-for": "1.1.1.1"}
    )  # forwarded header is not trusted: same client, sequence continues
    assert other.status_code == 500
    assert client.get("/flaky?codes=204").content == b""


def test_flaky_seeded_rate_is_reproducible(client_factory: Callable[..., TestClient]) -> None:
    def run() -> list[int]:
        client = client_factory()
        return [client.get("/flaky?error_rate=50&seed=7").status_code for _ in range(10)]

    first = run()
    assert first == run()
    assert set(first) <= {200, 500}


def test_flaky_validation(client: TestClient) -> None:
    assert client.get("/flaky?codes=a,b").json()["error"] == "invalid_codes"
    assert client.get("/flaky?codes=,").json()["error"] == "invalid_codes"
    assert client.get("/flaky?codes=200,999").json()["error"] == "unsupported_status"


def test_flaky_store_bounds() -> None:
    store = FlakyStore(max_entries=2, idle_ttl=0.0)
    store.get("a").counter = 5
    assert store.get("a").counter == 0  # idle TTL elapsed: sequence restarts
    store.get("b")
    store.get("c")
    assert len(store) == 2


def test_response_builder(client: TestClient) -> None:
    resp = client.get(
        "/response",
        params={
            "status": 201,
            "body": "<b>hi</b>",
            "content_type": "text/html",
            "header": ["X-One: 1", "X-Two: 2"],
        },
    )
    assert resp.status_code == 201
    assert resp.text == "<b>hi</b>"
    assert resp.headers["x-two"] == "2"
    assert client.get("/response?status=204").content == b""
    assert client.get("/response?delay=1").content == b""
    bad = client.get("/response", params={"content_type": "not a mime"})
    assert bad.json()["error"] == "invalid_content_type"


def test_truncated_response_declares_more_than_it_sends(client: TestClient) -> None:
    # The in-process test transport does not enforce framing; real servers close
    # the connection early and clients see an incomplete read.
    resp = client.get("/truncated")
    assert int(resp.headers["content-length"]) > len(resp.content)
