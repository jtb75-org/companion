"""Add the login-subject read bootstrap clause to the users RLS policy

Revision ID: 036
Revises: 035

NOT INERT — this is a live change to the auth-critical `users` RLS policy and
applies regardless of ``auth_provider``. The Authentik BFF login resolves a
member by the stable OIDC subject (``external_subject_id``) BEFORE the tenant
user_id GUC exists; the prior policy only bootstrapped by ``id`` or ``email``, so
under FORCE RLS the by-subject SELECT failed closed (0 rows) for the NOBYPASSRLS
app role. This drops + recreates ``users_isolation`` from the updated
``users_isolation_statements()`` so USING also admits a row whose
``external_subject_id`` matches ``app.current_login_subject``.

Read-only bootstrap: the WITH CHECK is UNCHANGED (``id = app.current_user_id``) —
writes stay fenced to the tenant GUC, exactly like the email bootstrap. Same
drop+recreate pattern migration 029 used. Fully reversible: downgrade restores the
prior id/email-only policy.
"""

from alembic import op
from app.db.rls import users_isolation_statements

revision = "036"
down_revision = "035"

# The pre-036 policy text (id + email bootstrap only) — what downgrade restores.
_PRIOR_USING = (
    "id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
    "OR email = NULLIF(current_setting('app.current_login_email', true), '')"
)
_PRIOR_CHECK = "id = NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def upgrade() -> None:
    # Drop the existing (029) policy and recreate from the single-source statements
    # now carrying the subject-bootstrap USING clause. ENABLE/FORCE in those
    # statements are idempotent no-ops (already on from 029).
    op.execute("DROP POLICY IF EXISTS users_isolation ON users")
    for stmt in users_isolation_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS users_isolation ON users")
    op.execute(
        "CREATE POLICY users_isolation ON users "
        f"USING ({_PRIOR_USING}) "
        f"WITH CHECK ({_PRIOR_CHECK})"
    )
