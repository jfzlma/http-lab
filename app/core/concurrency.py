"""Bounded sleep helper with a global concurrency guard for slow endpoints."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException


async def guarded_sleep(semaphore: asyncio.Semaphore, ms: int) -> None:
    """Sleep *ms* milliseconds while bounding concurrent slow requests.

    When every slot is taken the request is shed immediately with 503 rather
    than queued, so an attacker cannot pile up an unbounded backlog of open
    connections behind the semaphore.
    """
    if semaphore.locked():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "too_many_slow_requests",
                "message": "the concurrent slow-request limit is reached; retry shortly",
            },
            headers={"Retry-After": "1"},
        )
    async with semaphore:
        await asyncio.sleep(ms / 1000)
