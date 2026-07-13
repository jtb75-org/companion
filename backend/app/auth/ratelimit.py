"""Login rate limiting (brute-force / credential-stuffing throttle).

PR #2 of the Firebase->Authentik migration — ADDITIVE AND INERT (used only by the
gated BFF ``/auth/login`` endpoint).

A fixed-window counter per bucket (username and client IP) in Redis. The login
endpoint increments both buckets per attempt and refuses (429) when either exceeds
the threshold; a successful login clears the username bucket. Keys are opaque
(no PII): the username is hashed, the IP is an IP. Mirrors the session-store shape
so tests can swap in an in-memory limiter.

Adapted from HealthCostClarity: reuses Companion's shared Redis pool
(``app.db.redis.get_redis``) and the ``companion:`` key namespace.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from app.config import settings
from app.db.redis import get_redis

_KEY = "companion:login:"


def _bucket_key(bucket: str) -> str:
    # Hash so a raw username never lands in Redis keys/logs.
    return _KEY + hashlib.sha256(bucket.encode()).hexdigest()[:32]


@runtime_checkable
class RateLimiter(Protocol):
    async def hit(self, bucket: str) -> int: ...
    async def reset(self, bucket: str) -> None: ...


class RedisRateLimiter:
    def __init__(self, redis, *, window: int) -> None:
        self._r = redis
        self._window = window

    async def hit(self, bucket: str) -> int:
        key = _bucket_key(bucket)
        count = await self._r.incr(key)
        if count == 1:  # first hit in this window → set the expiry
            await self._r.expire(key, self._window)
        return int(count)

    async def reset(self, bucket: str) -> None:
        await self._r.delete(_bucket_key(bucket))


class InMemoryRateLimiter:
    """Test double — no expiry; reset between tests by replacing the instance."""

    def __init__(self) -> None:
        self._d: dict[str, int] = {}

    async def hit(self, bucket: str) -> int:
        self._d[bucket] = self._d.get(bucket, 0) + 1
        return self._d[bucket]

    async def reset(self, bucket: str) -> None:
        self._d.pop(bucket, None)


_limiter: RateLimiter | None = None


def get_login_rate_limiter() -> RateLimiter:
    """Process-wide login limiter (looked up dynamically so tests can override)."""
    global _limiter
    if _limiter is None:
        _limiter = RedisRateLimiter(get_redis(), window=settings.login_window_seconds)
    return _limiter
