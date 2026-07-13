"""BFF session store (companion-authentik native login).

PR #2 of the Firebase->Authentik migration — ADDITIVE AND INERT. A BFF login
mints an opaque session id (the httpOnly cookie value) mapping to the
authenticated **subject** only. We store the Authentik per-provider ``sub`` hash
(NOT the email) so no PHI/PII lands in Redis; the subject is an opaque identifier,
not an identifier of the person. Sessions are server-side so they're revocable
(logout, account deletion) and carry a sliding TTL.

Adapted from HealthCostClarity: reuses Companion's shared Redis pool
(``app.db.redis.get_redis``) and the ``companion:`` key namespace.

NOTE (deferred to the cutover PR): no existing endpoint resolves this session yet.
The auth deps still read only the Firebase ``Authorization`` bearer. Once the
``external_subject_id`` column lands, ``get_current_user`` will resolve the
principal from the stored ``sub`` and this becomes the browser/app session of
record. Today it is minted but unconsumed.
"""

from __future__ import annotations

import secrets
from typing import Protocol, runtime_checkable

from app.config import settings
from app.db.redis import get_redis

_KEY = "companion:sess:"


@runtime_checkable
class SessionStore(Protocol):
    async def create(self, subject: str) -> str: ...
    async def get(self, sid: str) -> str | None: ...
    async def delete(self, sid: str) -> None: ...


class RedisSessionStore:
    def __init__(self, redis, *, ttl: int) -> None:
        self._r = redis
        self._ttl = ttl

    async def create(self, subject: str) -> str:
        sid = secrets.token_urlsafe(32)
        await self._r.set(_KEY + sid, subject, ex=self._ttl)
        return sid

    async def get(self, sid: str) -> str | None:
        subject = await self._r.get(_KEY + sid)
        if subject is not None:
            await self._r.expire(_KEY + sid, self._ttl)  # sliding
        return subject

    async def delete(self, sid: str) -> None:
        await self._r.delete(_KEY + sid)


class InMemorySessionStore:
    """Test double."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    async def create(self, subject: str) -> str:
        sid = secrets.token_urlsafe(32)
        self._d[sid] = subject
        return sid

    async def get(self, sid: str) -> str | None:
        return self._d.get(sid)

    async def delete(self, sid: str) -> None:
        self._d.pop(sid, None)


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Lazily-built process-wide session store (looked up dynamically so tests can
    override with an in-memory store)."""
    global _store
    if _store is None:
        _store = RedisSessionStore(get_redis(), ttl=settings.session_ttl_seconds)
    return _store
