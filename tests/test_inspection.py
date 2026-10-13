from __future__ import annotations

import re
from collections.abc import Callable

from fastapi.testclient import TestClient


def test_anything_echoes_json_body_and_query(client: TestClient) -> None:
    resp = client.post("/anything?a=1&a=2&b=", json={"hello": "world"})
    data = resp.json()
    assert resp.status_code == 200
    assert data["method"] == "POST"
    assert data["query"] == {"a": ["1", "2"], "b": ""}
    assert data["json"] == {"hello": "world"}
    assert data["protocol"] == "HTTP/1.1"
    assert data["tls"]["available"] is False
    assert data["request_id"] == resp.headers["x-request-id"]


def test_anything_form_and_binary_bodies(client: TestClient) -> None:
    form = client.post("/anything", data={"x": "1"}).json()
    assert form["form"] == {"x": "1"}
    binary = client.put(
        "/anything", content=b"\xff\xfe", headers={"content-type": "application/octet-stream"}
    ).json()
    assert binary["body_base64"] == "//4="
    assert "body" not in binary
    bad_json = client.post(
        "/anything", content=b"{nope", headers={"content-type": "application/json"}
    ).json()
    assert bad_json["body"] == "{nope"
    assert "json" not in bad_json


def test_anything_rejects_oversized_bodies(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(max_request_body_mb=1)
    declared = client.post("/anything", content=b"x" * (1024 * 1024 + 1))
    assert declared.status_code == 413
    assert declared.json()["error"] == "payload_too_large"

    def chunks():  # no Content-Length: enforced while streaming
        for _ in range(3):
            yield b"x" * (512 * 1024)

    streamed = client.post("/anything", content=chunks())
    assert streamed.status_code == 413
    assert streamed.json()["error"] == "payload_too_large"


def test_headers_breakdown(client: TestClient) -> None:
    data = client.get(
        "/headers", headers={"sec-fetch-mode": "cors", "x-forwarded-for": "1.2.3.4"}
    ).json()
    assert data["breakdown"]["sec_fetch"] == {"sec-fetch-mode": "cors"}
    assert data["breakdown"]["forwarding_headers"] == {"x-forwarded-for": "1.2.3.4"}


def test_ip_local_provider(client: TestClient) -> None:
    data = client.get("/ip").json()
    assert data["ip"] == "testclient"
    assert data["provider"] == "local"
    assert data["lookup_error"] == "address is not a valid IP"


def test_proxy_untrusted_forwarding(client: TestClient) -> None:
    data = client.get("/proxy", headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"}).json()
    assert data["client_ip"] == "testclient"
    assert data["client_ip_source"] == "socket"
    assert data["forwarded_for"] == ["9.9.9.9", "10.0.0.1"]
    assert data["proxy_detected"] is True
    assert data["inferred"]["xff_first_matches_socket"] is False


def test_proxy_trusted_forwarding_and_forwarded_header(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(trusted_proxy_count=1)
    data = client.get(
        "/proxy",
        headers={"x-forwarded-for": "9.9.9.9, 8.8.8.8", "forwarded": 'for="1.1.1.1";proto=https'},
    ).json()
    assert data["client_ip"] == "9.9.9.9"
    assert data["client_ip_source"] == "x-forwarded-for"
    assert data["forwarded_hosts"] == ["1.1.1.1"]
    assert "1 trusted" in data["trust_model"]


def test_fingerprint_reads_accept_encoding(client: TestClient) -> None:
    data = client.get(
        "/fingerprint",
        headers={"accept": "*/*", "accept-encoding": "gzip, br;q=0, zstd;q=0.5", "sec-ch-ua": "x"},
    ).json()
    assert data["headers"]["compression"] == {
        "gzip": True,
        "deflate": False,
        "br": False,
        "zstd": True,
    }
    assert data["http"]["version"] == "HTTP/1.1"
    assert "http2" not in data["signals"]
    assert "client_hints_present" in data["signals"]


def test_bot_signals_for_python_client(client: TestClient) -> None:
    data = client.get("/bot", headers={"user-agent": "python-httpx/0.27"}).json()
    signals = {s["name"]: s for s in data["signals"]}
    assert signals["python_http_client"]["detected"] is True
    assert signals["webdriver"]["detected"] is False
    assert signals["http2"]["detected"] is False
    assert data["client_ip"] == "testclient"


def test_ui_responses_require_revalidation(client: TestClient) -> None:
    for path in ("/", "/fingerprint/browser", "/static/app.js", "/static/style.css"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-cache", path
    html = client.get("/").text
    assert re.search(r'src="/static/app\.js\?v=[0-9a-f]{12}"', html)
    assert re.search(r'href="/static/style\.css\?v=[0-9a-f]{12}"', html)
    etag = client.get("/static/app.js").headers["etag"]
    assert client.get("/static/app.js", headers={"if-none-match": etag}).status_code == 304


def test_pages_and_ops(client: TestClient) -> None:
    assert "HTTP Lab" in client.get("/").text
    assert client.get("/fingerprint/browser").status_code == 200
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json() == {"status": "ready"}
