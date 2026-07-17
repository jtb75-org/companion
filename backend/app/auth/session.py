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
The auth deps still read only the Firebase ``Authorization`` bearer. The
``external_subject_id`` column now exists (PR #3) and the login path backfills it,
so ``get_current_user`` will resolve the principal by matching the stored ``sub``
against ``users.external_subject_id`` at cutover; this store keeps holding that
opaque subject. Today it is minted but unconsumed.

REVOCATION (pre-PHI gate #3 — session invalidation on password reset)
---------------------------------------------------------------------
A password reset must evict every live session of that account: the whole point of
resetting a suspected-compromised account is to throw the attacker out. Sessions are
opaque sids with no back-pointer from the subject, so "revoke all sessions for this
person" needs a mechanism.

We use an EPOCH (a not-before watermark) rather than a reverse index of sids:
  * each session value now carries the time it was minted (``iat``);
  * ``companion:sess:epoch:<subject>`` holds the subject's revocation watermark;
  * ``get`` refuses (and deletes) any session with ``iat < epoch``.
A reverse index (a SET of sids per subject) was considered and REJECTED: sessions carry
a SLIDING TTL, so a long-lived session can outlive its index entry and then be silently
missed by a revoke — a revoke that quietly fails to revoke is the worst possible bug on
this surface. The epoch is O(1), enumerates nothing, and cannot miss.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Protocol, runtime_checkable

from app.config import settings
from app.db.redis import get_redis

_KEY = "companion:sess:"
# Distinct keyspace for the per-subject revocation watermark. A sid is
# ``secrets.token_urlsafe`` (URL-safe base64 — no ':'), so a session key can never
# collide with an epoch key.
_EPOCH_KEY = "companion:sess:epoch:"


def _encode(subject: str, iat: float) -> str:
    """Session value: the opaque subject + when it was minted. Still no PII in Redis."""
    return json.dumps({"sub": subject, "iat": iat})


def _decode(raw: str) -> tuple[str, float] | None:
    """Parse a stored session value into ``(subject, iat)``, or ``None`` if unusable.

    FAIL CLOSED. Anything we cannot parse into a subject + a mint time — a legacy
    bare-string value written before the epoch scheme, corruption, a truncated write —
    is treated as no session. A value with no ``iat`` cannot be compared against the
    revocation epoch, so honouring it would create exactly the hole this exists to
    close: a session that survives a password reset. The caller deletes it."""
    try:
        data = json.loads(raw)
        subject = data["sub"]
        iat = float(data["iat"])
    except Exception:
        return None
    if not isinstance(subject, str) or not subject:
        return None
    return subject, iat


@runtime_checkable
class SessionStore(Protocol):
    async def create(self, subject: str) -> str: ...
    async def get(self, sid: str) -> str | None: ...
    async def delete(self, sid: str) -> None: ...
    async def revoke_all_for_subject(self, subject: str) -> None: ...


class RedisSessionStore:
    def __init__(self, redis, *, ttl: int) -> None:
        self._r = redis
        self._ttl = ttl

    async def create(self, subject: str) -> str:
        sid = secrets.token_urlsafe(32)
        await self._r.set(_KEY + sid, _encode(subject, time.time()), ex=self._ttl)
        return sid

    async def get(self, sid: str) -> str | None:
        """Resolve a live, non-revoked session to its subject (contract unchanged).

        Deletes the sid and returns None when the value is unusable (fail closed) or
        when the session predates its subject's revocation epoch."""
        raw = await self._r.get(_KEY + sid)
        if raw is None:
            return None
        parsed = _decode(raw)
        if parsed is None:
            await self._r.delete(_KEY + sid)  # legacy/corrupt → fail closed
            return None
        subject, iat = parsed
        epoch = await self._r.get(_EPOCH_KEY + subject)
        if epoch is not None and iat < float(epoch):
            await self._r.delete(_KEY + sid)  # revoked (e.g. password reset)
            return None
        await self._r.expire(_KEY + sid, self._ttl)  # sliding
        return subject

    async def delete(self, sid: str) -> None:
        await self._r.delete(_KEY + sid)

    async def revoke_all_for_subject(self, subject: str) -> None:
        """Invalidate every session minted for ``subject`` before now. O(1).

        TTL CORRECTNESS (do not "simplify" this away): the epoch key uses the SAME TTL
        as a session, and that is provably sufficient. A session revoked by this write
        has ``iat < epoch_time``; its own key expires at ``iat + T``, which is always
        EARLIER than the epoch's expiry at ``epoch_time + T``. It cannot slide past that
        deadline either, because refreshing a session requires a ``get`` — and ``get``
        deletes any session behind the epoch instead of refreshing it. So no revoked
        session can outlive its epoch and resurrect once the watermark expires."""
        await self._r.set(_EPOCH_KEY + subject, repr(time.time()), ex=self._ttl)


class InMemorySessionStore:
    """Test double. Mirrors RedisSessionStore's semantics exactly (encoded values,
    fail-closed parse, epoch check) so tests exercise the real revocation logic.
    Entries never expire here — TTL/sliding behaviour is Redis's job."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._epochs: dict[str, float] = {}

    async def create(self, subject: str) -> str:
        sid = secrets.token_urlsafe(32)
        self._d[sid] = _encode(subject, time.time())
        return sid

    async def get(self, sid: str) -> str | None:
        raw = self._d.get(sid)
        if raw is None:
            return None
        parsed = _decode(raw)
        if parsed is None:
            self._d.pop(sid, None)  # legacy/corrupt → fail closed
            return None
        subject, iat = parsed
        epoch = self._epochs.get(subject)
        if epoch is not None and iat < epoch:
            self._d.pop(sid, None)  # revoked
            return None
        return subject

    async def delete(self, sid: str) -> None:
        self._d.pop(sid, None)

    async def revoke_all_for_subject(self, subject: str) -> None:
        self._epochs[subject] = time.time()


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Lazily-built process-wide session store (looked up dynamically so tests can
    override with an in-memory store)."""
    global _store
    if _store is None:
        _store = RedisSessionStore(get_redis(), ttl=settings.session_ttl_seconds)
    return _store
