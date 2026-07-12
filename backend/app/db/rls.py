"""Row-Level Security policy DDL — per-user tenant isolation (WS1 Phase 2).

Single source of the standard per-user policy shape so migrations can't drift
from it. The tenant context is the transaction-local GUC ``app.current_user_id``
set by ``app/db/context.py`` (member/caregiver auth deps + per-user worker
sessions). Fail-closed: an unset/empty GUC becomes NULL via ``NULLIF(...,'')`` →
the predicate is false → zero rows AND zero writes.

Only tables whose access paths ALL set the GUC may be enabled — a path that
forgets it returns 0 rows. Global/admin/audit tables are left RLS-DISABLED (NOT
enabled-without-policy: FORCE RLS + no policy also fails closed).
"""

from __future__ import annotations

# NULLIF(..., '') + the `, true` (missing_ok) on current_setting is the whole
# fail-closed trick: unset/empty GUC → NULL → predicate false.
_USER_GUC = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def tenant_isolation_statements(table: str, *, user_col: str = "user_id") -> list[str]:
    """ENABLE + FORCE RLS + the standard per-user isolation policy for ``table``.

    FORCE so the policy applies even to a table owner; the app connects as the
    non-owner NOBYPASSRLS ``companion_app`` role, and cross-user maintenance runs
    under the ``companion_maintenance`` BYPASSRLS role.
    """
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_isolation ON {table} "
        f"USING ({user_col} = {_USER_GUC}) "
        f"WITH CHECK ({user_col} = {_USER_GUC})",
    ]


def drop_isolation_statements(table: str) -> list[str]:
    """Reverse of ``tenant_isolation_statements`` (for migration downgrade)."""
    return [
        f"DROP POLICY IF EXISTS {table}_isolation ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]
