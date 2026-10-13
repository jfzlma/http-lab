"""Application settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for HTTP Lab.

    All values can be overridden through environment variables or a `.env` file
    (see `.env.example`). No secrets should ever be committed to the repository.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- General ---
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    log_requests: bool = True

    # --- Safety limits ---
    max_delay_ms: int = 30_000
    max_redirects: int = 20
    max_response_size_mb: int = 10
    max_request_body_mb: int = 1
    concurrent_slow_requests: int = 50

    # --- Global rate limiting ---
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 120
    rate_limit_window: int = 60
    redis_url: str | None = None

    # --- Client IP resolution ---
    trusted_proxy_count: int = 0

    # --- IP intelligence ---
    ip_intelligence_provider: str = "local"
    ip_intelligence_api_key: str | None = None
    ip_info_timeout: float = 2.0
    ip_info_cache_ttl: int = 3600

    # --- CORS ---
    cors_origins: str = ""

    @field_validator("max_delay_ms", "max_redirects", "max_response_size_mb", "max_request_body_mb")
    @classmethod
    def _positive_limits(cls, value: int) -> int:
        if value < 1:
            raise ValueError("limits must be >= 1")
        return value

    @field_validator("concurrent_slow_requests", "rate_limit_requests", "rate_limit_window")
    @classmethod
    def _at_least_one(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be >= 1")
        return value

    @field_validator("log_level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unknown log level: {value!r}")
        return level

    @field_validator("trusted_proxy_count")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be >= 0")
        return value

    @field_validator("ip_intelligence_provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider not in {"local", "ipinfo", "ipapi"}:
            raise ValueError(f"unknown IP intelligence provider: {value!r}")
        return provider

    @property
    def max_response_size_bytes(self) -> int:
        return self.max_response_size_mb * 1024 * 1024

    @property
    def max_request_body_bytes(self) -> int:
        return self.max_request_body_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (pydantic-settings is cheap to cache)."""
    return Settings()
