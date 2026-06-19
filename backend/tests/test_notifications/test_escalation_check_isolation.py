"""Per-user isolation in the safety-critical escalation worker.

One malformed user record must not suppress every other user's caregiver
escalation for the cycle. The worker checks + commits each user
independently, counts failures, and never raises on a single-user error.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.db import session as db_module
from app.models.enums import AccountStatus
from app.models.user import User
from app.workers.escalation_check import run_escalation_check
from tests.conftest import requires_db

pytestmark = requires_db

_EMAILS = [
    "esc-iso-bad@example.com",
    "esc-iso-good-1@example.com",
    "esc-iso-good-2@example.com",
]


async def _seed_users() -> dict[str, object]:
    """Create three active users; return email -> id map."""
    await _cleanup()
    ids: dict[str, object] = {}
    async with db_module.async_session_factory() as s:
        for email in _EMAILS:
            u = User(
                email=email,
                preferred_name="Esc",
                display_name="Esc Test",
                account_status=AccountStatus.ACTIVE,
            )
            s.add(u)
            await s.flush()
            ids[email] = u.id
        await s.commit()
    return ids


async def _cleanup():
    async with db_module.async_session_factory() as s:
        await s.execute(delete(User).where(User.email.in_(_EMAILS)))
        await s.commit()


async def test_one_bad_user_does_not_block_others(monkeypatch):
    ids = await _seed_users()
    bad_id = ids["esc-iso-bad@example.com"]
    good_ids = {
        ids["esc-iso-good-1@example.com"],
        ids["esc-iso-good-2@example.com"],
    }

    calls: list[object] = []

    async def _fake_check_escalations(db, user_id):
        calls.append(user_id)
        if user_id == bad_id:
            raise RuntimeError("malformed user record")
        if user_id in good_ids:
            # Each healthy seeded user produces one escalation.
            return [{"question_id": "q", "contacts_notified": 1}]
        # Any other pre-existing active user: nothing to escalate.
        return []

    monkeypatch.setattr(
        "app.workers.escalation_check.check_escalations",
        _fake_check_escalations,
    )

    # Must not raise despite the one bad user.
    result = await run_escalation_check()

    # All three seeded users were attempted — the bad user did not abort
    # the loop before the good users ran.
    for uid in ids.values():
        assert uid in calls

    # Both healthy seeded users escalated; the bad one was counted failed
    # and rolled back in isolation. Other pre-existing active users (e.g.
    # the conftest test user) return [] and do not affect these counts.
    assert result["total_escalated"] >= 2
    assert result["failed"] >= 1
    assert result["users_checked"] >= 3

    await _cleanup()


async def test_total_failure_raises(monkeypatch):
    """A systemic failure (every user fails) must raise, not exit clean."""
    await _seed_users()

    async def _always_fail(db, user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.workers.escalation_check.check_escalations", _always_fail
    )

    with pytest.raises(RuntimeError):
        await run_escalation_check()

    await _cleanup()
