"""app.db.grants: the audit tables are append-only for the runtime role (PR C, §5).

No live DB / no companion_app role needed (CI connects as the owner and does not run
grants.py) — we assert the statement set and that apply_grants REVOKEs UPDATE/DELETE on
the audit tables AFTER the broad GRANT, via a mocked engine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.db import grants

_BROAD_GRANT = "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES"


def test_revoke_statements_cover_both_audit_tables():
    assert grants._APPEND_ONLY_AUDIT_TABLES == (
        "caregiver_activity_log",
        "account_audit_log",
    )
    for table in grants._APPEND_ONLY_AUDIT_TABLES:
        assert (
            f"REVOKE UPDATE, DELETE ON {table} FROM {grants.APP_ROLE}"
            in grants._REVOKE_STATEMENTS
        )
    # Append-only is a REVOKE layered on top of the still-present broad DML grant.
    assert any(_BROAD_GRANT in s for s in grants._GRANT_STATEMENTS)


def test_maintenance_regrant_covers_only_account_audit_log():
    """The BYPASSRLS maintenance role inherits the append-only REVOKE (it is a member of
    APP_ROLE), so it must be re-granted DELETE on account_audit_log for the retention
    purge — but NOT on caregiver_activity_log, which stays immutable for every role."""
    assert grants._MAINT_REGRANT_STATEMENTS == (
        f"GRANT DELETE ON account_audit_log TO {grants.MAINT_ROLE}",
    )
    # Guard the invariant that we never hand the maintenance role DELETE on the other
    # append-only table.
    assert not any(
        "caregiver_activity_log" in s for s in grants._MAINT_REGRANT_STATEMENTS
    )


def test_caregiver_activity_log_relationships_defer_to_db_cascade():
    """Append-only requires that a user/contact deletion NOT emit an ORM DELETE on
    caregiver_activity_log as companion_app (which now lacks DELETE). Both ORM
    relationships to that table MUST set passive_deletes=True so SQLAlchemy defers to
    the FK's DB-level ON DELETE CASCADE (owner-run). Guards against re-introducing the
    grace=0 member-self-serve-deletion permission-denied break (PR #83 / safety BLOCK)."""
    from app.models.trusted_contact import TrustedContact
    from app.models.user import User

    # User.caregiver_activity_logs (user_id FK, ON DELETE CASCADE): True defers the
    # cascade DELETE to the DB.
    assert (
        User.__mapper__.relationships["caregiver_activity_logs"].passive_deletes is True
    )
    # TrustedContact.activity_logs (trusted_contact_id FK, ON DELETE SET NULL): must be
    # "all", not True — True still emits an ORM UPDATE ... SET NULL on a LOADED
    # collection, which would 500 under the append-only REVOKE (companion_app lacks
    # UPDATE). "all" fully defers the FK-nulling to the owner-run DB SET NULL.
    assert (
        TrustedContact.__mapper__.relationships["activity_logs"].passive_deletes
        == "all"
    )


async def test_apply_grants_revokes_after_granting(monkeypatch):
    """The broad GRANTs run first, then the audit-table REVOKEs — order matters, since a
    REVOKE before ``GRANT ... ON ALL TABLES`` would be undone by that grant."""
    executed: list[str] = []

    class _Conn:
        async def execute(self, stmt, *args, **kwargs):
            executed.append(str(stmt))

    class _Begin:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *args):
            return False

    class _Engine:
        def begin(self):
            return _Begin()

        async def dispose(self):
            return None

    monkeypatch.setattr(grants, "create_async_engine", lambda *a, **k: _Engine())
    monkeypatch.setattr(grants, "_role_exists", AsyncMock(return_value=True))

    await grants.apply_grants(retries=1, delay=0)

    grant_idx = next(i for i, s in enumerate(executed) if _BROAD_GRANT in s)
    revoke_idxs = [
        i for i, s in enumerate(executed) if s.startswith("REVOKE UPDATE, DELETE ON")
    ]
    assert len(revoke_idxs) == 2
    assert all(ri > grant_idx for ri in revoke_idxs), "REVOKE must run after the grant"
    for table in grants._APPEND_ONLY_AUDIT_TABLES:
        assert (
            f"REVOKE UPDATE, DELETE ON {table} FROM {grants.APP_ROLE}" in executed
        )
    # The maintenance re-grant must run AFTER the append-only REVOKE (a re-grant before
    # the REVOKE would be a no-op/undone), restoring the retention purge for that role.
    regrant = f"GRANT DELETE ON account_audit_log TO {grants.MAINT_ROLE}"
    assert regrant in executed
    assert executed.index(regrant) > max(revoke_idxs), (
        "maintenance re-grant must run after the append-only REVOKE"
    )
