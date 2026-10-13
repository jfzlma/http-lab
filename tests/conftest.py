"""Shared fixtures: every test gets a fresh app built from explicit settings."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def make_settings(**overrides: Any) -> Settings:
    """Settings that ignore the developer's .env and environment-specific values."""
    base: dict[str, Any] = {"app_env": "test", "log_requests": False}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def client_factory() -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(**overrides: Any) -> TestClient:
        client = TestClient(create_app(make_settings(**overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(client_factory: Callable[..., TestClient]) -> TestClient:
    return client_factory()
