"""Add purge_signup_refused_audit() — DB-enforced scoped purge of the audit log

Revision ID: 041
Revises: 040

The retention worker must delete transient ``signup_refused`` rows (rejected-signup
email PII, no member behind them) from ``account_audit_log`` on a retention window, but
that table is append-only for the runtime roles (grants.py REVOKEs UPDATE/DELETE) to
keep the real-member audit trail (``account_activated`` rows) immutable.

Until now the maintenance role was handed table-level DELETE, so the ``signup_refused``
scope was enforced ONLY by the worker's WHERE-clause — and companion_maintenance also
backs the admin HTTP surface, so an admin-path bug could have deleted real-member rows.

This function moves the scope into the DATABASE: it is owned by the table owner
(``companion``, SECURITY DEFINER) and hardcodes ``event = 'signup_refused'``, so no
matter who calls it, ONLY transient pre-auth rows can be removed. In the follow-up
change (PR2) the table-level DELETE grant is revoked from every runtime role, leaving
this function the sole path by which any row can leave account_audit_log.

EXECUTE is revoked from PUBLIC here; grants.py grants it to the maintenance role
(role-existence-guarded, re-applied every deploy). ``SET search_path`` is pinned so the
SECURITY DEFINER body cannot be hijacked via a caller-controlled search_path.

Reversible: downgrade drops the function.
"""

from alembic import op

revision = "041"
down_revision = "040"


_CREATE_FN = """
CREATE OR REPLACE FUNCTION purge_signup_refused_audit(cutoff timestamptz)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    deleted integer;
BEGIN
    DELETE FROM public.account_audit_log
    WHERE event = 'signup_refused'
      AND occurred_at < cutoff;
    GET DIAGNOSTICS deleted = ROW_COUNT;
    RETURN deleted;
END;
$$;
"""

# New functions default to EXECUTE for PUBLIC; lock that down immediately. grants.py
# re-grants EXECUTE to the maintenance role (the only caller — the retention worker).
_REVOKE_PUBLIC = (
    "REVOKE EXECUTE ON FUNCTION purge_signup_refused_audit(timestamptz) FROM PUBLIC"
)


def upgrade() -> None:
    op.execute(_CREATE_FN)
    op.execute(_REVOKE_PUBLIC)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS purge_signup_refused_audit(timestamptz)")
