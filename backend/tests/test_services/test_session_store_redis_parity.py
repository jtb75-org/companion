"""RedisSessionStore parity — the PRODUCTION store, not the in-memory double.

The rest of the session-revocation suite runs against ``InMemorySessionStore``. That
double is hand-written, so it can silently drift from the Redis implementation that
actually runs in prod — and the epoch/not-before logic it models is a security control
(a password reset must evict live sessions). A revoke that quietly fails to revoke is
the failure mode the whole design exists to prevent, so the real store needs its own
teeth in CI.

fakeredis (not a hand-rolled stub) so real Redis semantics are emulated — notably
``decode_responses=True``, since the store round-trips ``iat``/epoch floats through
strings, and TTLs.
"""

from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from app.auth.session import _EPOCH_KEY, _KEY, RedisSessionStore

_TTL = 60


@pytest.fixture
def store() -> RedisSessionStore:
    # decode_responses=True mirrors app/db/redis.py's pool.
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return RedisSessionStore(fake, ttl=_TTL)


async def test_revoke_evicts_every_session_for_the_subject(store: RedisSessionStore):
    sid_a = await store.create("subject-1")
    sid_b = await store.create("subject-1")
    assert await store.get(sid_a) == "subject-1"
    assert await store.get(sid_b) == "subject-1"

    await asyncio.sleep(0.01)  # ensure epoch > iat
    await store.revoke_all_for_subject("subject-1")

    assert await store.get(sid_a) is None
    assert await store.get(sid_b) is None
    # ...and the keys are physically gone, not merely reported invalid.
    assert await store._r.get(_KEY + sid_a) is None
    assert await store._r.get(_KEY + sid_b) is None


async def test_revoke_is_scoped_to_one_subject(store: RedisSessionStore):
    mine = await store.create("subject-1")
    theirs = await store.create("subject-2")

    await asyncio.sleep(0.01)
    await store.revoke_all_for_subject("subject-1")

    assert await store.get(mine) is None
    assert await store.get(theirs) == "subject-2", "revoked the wrong person's session"


async def test_session_minted_after_a_revoke_survives(store: RedisSessionStore):
    """The epoch must not brick the account — the point is a reset, then a fresh login."""
    old = await store.create("subject-1")
    await asyncio.sleep(0.01)
    await store.revoke_all_for_subject("subject-1")
    await asyncio.sleep(0.01)

    fresh = await store.create("subject-1")
    assert await store.get(old) is None
    assert await store.get(fresh) == "subject-1"


async def test_legacy_bare_string_fails_closed(store: RedisSessionStore):
    """Pre-epoch values carry no iat, so they cannot be proven to predate a revoke."""
    await store._r.set(_KEY + "legacy-sid", "raw-subject-no-iat", ex=_TTL)
    assert await store.get("legacy-sid") is None
    assert await store._r.get(_KEY + "legacy-sid") is None, "should be cleaned up"


async def test_corrupt_epoch_fails_closed(store: RedisSessionStore):
    """An unreadable epoch must deny, not 500 and not grant."""
    sid = await store.create("subject-1")
    await store._r.set(_EPOCH_KEY + "subject-1", "not-a-float", ex=_TTL)
    assert await store.get(sid) is None  # must not raise


async def test_epoch_ttl_is_not_shorter_than_the_session_ttl(store: RedisSessionStore):
    """Correctness rests on the epoch outliving any session it must revoke.

    If the epoch expired first, a revoked-but-untouched session could resurrect. Sessions
    can't outlive it in practice (refreshing requires a get(), which deletes a revoked
    sid), but the epoch must never be given a SHORTER ttl than a session.
    """
    await store.create("subject-1")
    await store.revoke_all_for_subject("subject-1")
    assert await store._r.ttl(_EPOCH_KEY + "subject-1") >= _TTL - 1


async def test_sliding_ttl_still_refreshes(store: RedisSessionStore):
    sid = await store.create("subject-1")
    await store._r.expire(_KEY + sid, 5)  # simulate age
    assert await store.get(sid) == "subject-1"
    assert await store._r.ttl(_KEY + sid) > 5, "get() should refresh the sliding TTL"


async def test_delete_removes_the_session(store: RedisSessionStore):
    sid = await store.create("subject-1")
    await store.delete(sid)
    assert await store.get(sid) is None
