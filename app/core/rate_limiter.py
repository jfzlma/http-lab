"""Rate limiting primitives.

A minimal sliding-window limiter backed by in-process memory by default, with
an optional Redis-backed fixed-window implementation for multi-worker
deployments. Redis is only used when ``REDIS_URL`` is configured.

Note: the in-memory limiter is per-process. With multiple uvicorn workers,
enable ``REDIS_URL`` to enforce limits across workers.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


def retry_after_seconds(seconds: float) -> int:
    """Whole seconds for a Retry-After header: rounded up, at least 1."""
    return max(1, math.ceil(seconds))


@dataclass(frozen=True)
class RateLimitState:
    """Result of a rate-limit check for one key."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: float  # epoch seconds when the window resets (RFC-safe for headers)
    retry_after: float  # seconds a client should wait before retrying


class RateLimiter(Protocol):
    """Common interface for rate limiter backends."""

    async def check(self, key: str, limit: int, window: float) -> RateLimitState: ...
    async def close(self) -> None: ...


class MemoryRateLimiter:
    """Sliding-window log limiter stored in process memory."""

    def __init__(self, max_keys: int = 10_000) -> None:
        self._max_keys = max_keys
        self._windows: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str, limit: int, window: float) -> RateLimitState:
        now = time.monotonic()
        async with self._lock:
            queue = self._windows.get(key)
            if queue is None:
                if len(self._windows) >= self._max_keys:
                    self._evict_stale(now, window)
                queue = deque()
                self._windows[key] = queue
            cutoff = now - window
            while queue and queue[0] <= cutoff:
                queue.popleft()
            # Window math uses the monotonic clock; reset_at is reported in
            # wall-clock epoch seconds because it ends up in X-RateLimit-Reset.
            to_epoch = time.time() - now
            if len(queue) >= limit:
                frees_at = queue[0] + window if queue else now + window
                return RateLimitState(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    reset_at=frees_at + to_epoch,
                    retry_after=max(0.0, frees_at - now),
                )
            queue.append(now)
            # A slot frees up when the oldest request in the window expires.
            return RateLimitState(
                allowed=True,
                limit=limit,
                remaining=max(0, limit - len(queue)),
                reset_at=queue[0] + window + to_epoch,
                retry_after=0.0,
            )

    def _evict_stale(self, now: float, window: float) -> None:
        stale = [k for k, q in self._windows.items() if not q or q[-1] <= now - window]
        for key in stale:
            del self._windows[key]

    async def close(self) -> None:
        self._windows.clear()


class RedisRateLimiter:
    """Fixed-window limiter backed by Redis (multi-worker safe)."""

    def __init__(self, url: str) -> None:
        try:
            import redis.asyncio as aioredis  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise RuntimeError("REDIS_URL is set but the 'redis' extra is not installed") from exc
        self._client = aioredis.from_url(url, decode_responses=True)

    async def check(self, key: str, limit: int, window: float) -> RateLimitState:
        redis_key = f"httplab:rl:{key}"
        window_s = max(1, int(window))
        # One MULTI/EXEC round trip: create the window with its expiry atomically
        # (SET NX EX), then count. A separate INCR + EXPIRE could leave a key with
        # no TTL if the process died in between, locking the client out forever.
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.set(redis_key, 0, ex=window_s, nx=True)
            pipe.incr(redis_key)
            pipe.ttl(redis_key)
            _, count, ttl = await pipe.execute()
        ttl = ttl if ttl and ttl > 0 else window_s
        allowed = count <= limit
        return RateLimitState(
            allowed=allowed,
            limit=limit,
            remaining=max(0, limit - count),
            reset_at=time.time() + ttl,
            retry_after=0.0 if allowed else float(ttl),
        )

    async def close(self) -> None:
        await self._client.aclose()


def build_rate_limiter(redis_url: str | None) -> RateLimiter:
    """Instantiate the configured limiter, falling back to memory on failure."""
    if redis_url:
        try:
            return RedisRateLimiter(redis_url)
        except (RuntimeError, ImportError, ValueError):
            logger.warning("Redis rate limiting unavailable; falling back to in-memory limiter")
    return MemoryRateLimiter()
