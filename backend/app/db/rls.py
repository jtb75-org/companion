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
# The bootstrap email GUC — set before the user_id is known (auth-by-email). Same
# fail-closed idiom: unset/empty → NULL → `email = NULL` → false.
_LOGIN_EMAIL_GUC = "NULLIF(current_setting('app.current_login_email', true), '')"
# The bootstrap subject GUC — the stable OIDC `sub`, set before the user_id is
# known on the Authentik login path (auth-by-subject; app/api/auth_authentik.py).
# Same fail-closed idiom: unset/empty → NULL → `external_subject_id = NULL` → false.
_LOGIN_SUBJECT_GUC = "NULLIF(current_setting('app.current_login_subject', true), '')"


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


def users_isolation_statements() -> list[str]:
    """ENABLE + FORCE RLS + the `users`-table isolation policy.

    `users` is special: it is keyed on ``id`` (not ``user_id``) and needs
    read-only bootstrap clauses because auth resolves a member by email (Firebase)
    or by stable OIDC subject (Authentik) *before* the user_id GUC exists. So a row
    is READABLE when its id matches the tenant GUC OR its email matches the
    login-email GUC OR its external_subject_id matches the login-subject GUC, but
    WRITES are fenced to the tenant GUC only (``id = app.current_user_id``) — the
    bootstrap clauses must never authorize a write. Cross-user reads/writes (admin,
    invitation stubs) run under the BYPASSRLS ``companion_maintenance`` role.
    """
    return [
        "ALTER TABLE users ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE users FORCE ROW LEVEL SECURITY",
        "CREATE POLICY users_isolation ON users "
        f"USING (id = {_USER_GUC} OR email = {_LOGIN_EMAIL_GUC} "
        f"OR external_subject_id = {_LOGIN_SUBJECT_GUC}) "
        f"WITH CHECK (id = {_USER_GUC})",
    ]


def drop_users_isolation_statements() -> list[str]:
    """Reverse of ``users_isolation_statements`` (for migration downgrade)."""
    return [
        "DROP POLICY IF EXISTS users_isolation ON users",
        "ALTER TABLE users NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE users DISABLE ROW LEVEL SECURITY",
    ]


def drop_isolation_statements(table: str) -> list[str]:
    """Reverse of ``tenant_isolation_statements`` (for migration downgrade)."""
    return [
        f"DROP POLICY IF EXISTS {table}_isolation ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]
