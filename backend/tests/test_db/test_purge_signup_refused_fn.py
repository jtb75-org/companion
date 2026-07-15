"""Integration test for purge_signup_refused_audit() (migration 041).

The SECURITY DEFINER function is the DB-enforced scope introduced in PR1: it must delete
ONLY old `signup_refused` rows and must NEVER touch `account_activated` (real-member)
audit rows, regardless of their age. This is the property that lets PR2 revoke
table-level DELETE from every runtime role without losing the retention purge.

Requires a live DB with migrations applied (CI connects as the owner, who can EXECUTE
the function); skipped otherwise via `requires_db`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import DateTime, bindparam, delete, select, text

from app.db import session as db_module
from app.models.audit import AccountAuditLog
from tests.conftest import requires_db

pytestmark = requires_db

_EMAIL = "purge-fn-test@example.invalid"


async def _events(email: str) -> list[str]:
    async with db_module.async_session_factory() as s:
        rows = (
            await s.execute(
                select(AccountAuditLog).where(AccountAuditLog.email == email)
            )
        ).scalars().all()
        return sorted(r.event for r in rows)


async def _cleanup(email: str) -> None:
    async with db_module.async_session_factory() as s:
        await s.execute(delete(AccountAuditLog).where(AccountAuditLog.email == email))
        await s.commit()


async def test_purge_function_scopes_to_old_signup_refused_only():
    await _cleanup(_EMAIL)
    now = datetime.utcnow()
    old = now - timedelta(days=200)
    recent = now - timedelta(days=1)
    async with db_module.async_session_factory() as s:
        s.add_all(
            [
                # Old refused signup — the only row that should be purged.
                AccountAuditLog(event="signup_refused", email=_EMAIL, occurred_at=old),
                # Recent refused signup — inside the window, must survive.
                AccountAuditLog(
                    event="signup_refused", email=_EMAIL, occurred_at=recent
                ),
                # OLD real-member audit — must survive despite its age (scope check).
                AccountAuditLog(
                    event="account_activated", email=_EMAIL, occurred_at=old
                ),
            ]
        )
        await s.commit()

    cutoff = now - timedelta(days=90)
    async with db_module.async_session_factory() as s:
        deleted = (
            await s.execute(
                text("SELECT purge_signup_refused_audit(:cutoff)").bindparams(
                    bindparam("cutoff", type_=DateTime(timezone=True))
                ),
                {"cutoff": cutoff},
            )
        ).scalar()
        await s.commit()

    # The function is a global sweep, so other stale rows may also be counted; assert it
    # removed at least our old row, then prove the exact per-email outcome.
    assert deleted >= 1
    # Old signup_refused is gone; recent signup_refused AND the old account_activated row
    # remain — the DB-enforced scope never deletes real-member audit rows.
    assert await _events(_EMAIL) == ["account_activated", "signup_refused"]
    await _cleanup(_EMAIL)
