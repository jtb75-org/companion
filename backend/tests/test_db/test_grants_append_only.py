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
