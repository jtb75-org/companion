"""execute_deletion step-10 Authentik account cleanup (IdP orphan close).

The member-deletion flow hard-DELETEs the Authentik account for the deleted email
(CCPA right-to-delete + stale-password-on-reinvite hazard). The call is BEST-EFFORT:
the local rows are the primary deletion, so an Authentik/network failure must not
abort the request. These tests assert the integration is invoked with the deleted
user's email, the outcome is recorded in the audit, and a raising integration yields
``idp_cleanup == "failed"`` while the user row is still deleted.

Hermetic: the integration function is mocked — no real Authentik is ever contacted.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, select

import app.services.account_lifecycle_service as als
from app.db import session as db_module
from app.db.session import maintenance_session
from app.models.audit import DeletionAuditLog
from app.models.enums import AccountStatus
from app.models.user import User
from tests.conftest import requires_db


def _stub_side_effects(monkeypatch):
    monkeypatch.setattr(als, "delete_storage_objects", lambda uris: (0, 0))

    async def _noop_redis(user_id):
        return 0

    monkeypatch.setattr(als, "clear_redis_keys", _noop_redis)


async def _make_active_user(email: str):
    async with db_module.async_session_factory() as s:
        user = User(
            email=email,
            preferred_name="D",
            display_name="D",
            account_status=AccountStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        return user.id


async def _fetch_audit_details(uid):
    async with db_module.async_session_factory() as s:
        audit = (
            await s.execute(
                select(DeletionAuditLog).where(DeletionAuditLog.user_id == uid)
            )
        ).scalar_one()
        return audit.details


async def _cleanup(uid, email):
    async with db_module.async_session_factory() as s:
        await s.execute(delete(DeletionAuditLog).where(DeletionAuditLog.user_id == uid))
        await s.commit()


@requires_db
async def test_execute_deletion_invokes_authentik_delete_with_email(monkeypatch):
    """delete_authentik_account is called with the deleted user's email, and its
    outcome is persisted to the audit."""
    _stub_side_effects(monkeypatch)

    seen: list[str] = []

    async def _spy_delete(email):
        seen.append(email)
        return "deleted"

    monkeypatch.setattr(als, "delete_authentik_account", _spy_delete)

    email = f"idp-{uuid4().hex[:8]}@example.com"
    uid = await _make_active_user(email)

    async with maintenance_session() as db:
        await als.execute_deletion(db, uid)
        await db.commit()

    assert seen == [email]  # invoked once, with the exact deleted email

    details = await _fetch_audit_details(uid)
    assert details.get("idp_cleanup") == "deleted"

    await _cleanup(uid, email)


@requires_db
async def test_execute_deletion_best_effort_when_authentik_raises(monkeypatch):
    """If the IdP delete RAISES, the outcome is 'failed' AND the account deletion still
    completes (user row is gone) — the local deletion is never rolled back."""
    _stub_side_effects(monkeypatch)

    async def _boom_delete(email):
        raise RuntimeError("authentik unreachable")

    monkeypatch.setattr(als, "delete_authentik_account", _boom_delete)

    email = f"idp-fail-{uuid4().hex[:8]}@example.com"
    uid = await _make_active_user(email)

    async with maintenance_session() as db:
        await als.execute_deletion(db, uid)
        await db.commit()

    details = await _fetch_audit_details(uid)
    assert details.get("idp_cleanup") == "failed"

    # The user row is still deleted despite the IdP failure.
    async with db_module.async_session_factory() as s:
        assert (await s.get(User, uid)) is None

    await _cleanup(uid, email)
