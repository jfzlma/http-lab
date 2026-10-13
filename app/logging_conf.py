"""Logging setup with privacy-conscious redaction helpers.

HTTP Lab is privacy-conscious by design:
  * Authorization / Cookie / API-key style headers are redacted in logs.
  * Sensitive query parameters (token, key, secret, ...) are redacted.
  * Request bodies are NEVER logged.
  * ``LOG_REQUESTS=false`` disables request logging entirely.
"""

from __future__ import annotations

import logging
import urllib.parse

from .config import Settings

SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-access-token",
        "x-amz-security-token",
        "x-id-token",
    }
)

SENSITIVE_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "pwd",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "client_secret",
        "auth",
        "authorization",
        "credential",
        "signature",
    }
)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _sanitize(value: str) -> str:
    """Strip CR/LF sequences to prevent log injection."""
    return value.replace("\r", "\\r").replace("\n", "\\n")


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with sensitive values replaced."""
    return {
        name: ("[REDACTED]" if name.lower() in SENSITIVE_HEADERS else _sanitize(value))
        for name, value in headers.items()
    }


def redact_query_string(query: str) -> str:
    """Return *query* with sensitive parameter values replaced."""
    if not query:
        return query
    params = urllib.parse.parse_qsl(query, keep_blank_values=True)
    return urllib.parse.urlencode(
        [
            (k, "[REDACTED]" if k.lower() in SENSITIVE_QUERY_KEYS else _sanitize(v))
            for k, v in params
        ]
    )


def setup_logging(settings: Settings) -> None:
    """Configure the root logger.

    The `uvicorn.access` logger is muted by default because HTTP Lab logs
    requests itself (with redaction) via RequestContextMiddleware.
    """
    root = logging.getLogger()
    if root.handlers:
        # Already configured (e.g. test session) — just sync the level.
        root.setLevel(settings.log_level.upper())
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
