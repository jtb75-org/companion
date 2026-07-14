"""execute_deletion audit-details completeness (JSONB mutation-tracking fix).

Regression: audit_details is mutated in-place AFTER db.add(audit), and the admin_users
lookup mid-function triggers an autoflush that serialized a PARTIAL dict. Without
flag_modified, the late keys (firebase_auth_deleted / admin_record_deleted) were dropped
from the persisted deletion_audit_log.
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


@requires_db
async def test_execute_deletion_persists_full_audit_details(monkeypatch):
    """The persisted deletion audit includes firebase_auth_deleted (a key set AFTER the
    mid-function autoflush) — proving the complete details dict is written, not the
    partial snapshot."""
    import app.auth.firebase as fb

    monkeypatch.setattr(fb, "delete_firebase_user", lambda email: True)
    monkeypatch.setattr(als, "delete_storage_objects", lambda uris: (0, 0))

    async def _noop_redis(user_id):
        return 0

    monkeypatch.setattr(als, "clear_redis_keys", _noop_redis)

    email = f"del-{uuid4().hex[:8]}@example.com"
    async with db_module.async_session_factory() as s:
        user = User(
            email=email,
            preferred_name="D",
            display_name="D",
            account_status=AccountStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        uid = user.id

    async with maintenance_session() as db:
        await als.execute_deletion(db, uid)
        await db.commit()

    async with db_module.async_session_factory() as s:
        audit = (
            await s.execute(
                select(DeletionAuditLog).where(DeletionAuditLog.user_id == uid)
            )
        ).scalar_one()
        details = audit.details

    # The late-added key survived (the fix); plus earlier keys still present.
    assert details.get("firebase_auth_deleted") is True
    assert details.get("email") == email
    assert details.get("caregiver_roles_removed") == 0

    async with db_module.async_session_factory() as s:
        await s.execute(delete(DeletionAuditLog).where(DeletionAuditLog.user_id == uid))
        await s.commit()
