"""Enable per-user RLS on the users table (WS1 Phase 2e — bootstrap table)

Revision ID: 029
Revises: 028

The auth-critical table, enabled last. `users` gets the dual-clause policy: a row
is READABLE when its id matches app.current_user_id OR its email matches
app.current_login_email (the pre-user-id auth-by-email bootstrap), but WRITES are
fenced to the tenant GUC (id = app.current_user_id) so the email bootstrap can
never authorize a write.

Prerequisites landed in #57 (GUC/maintenance wiring) and the follow-up
(profile.py DEK GUC, invitations.py maintenance reads). An access-path audit
confirmed every users read/write is either GUC-bootstrapped on the request
session (auth deps, profile, complete-profile) or runs under the BYPASSRLS
companion_maintenance role (admin, invitation stubs, workers' cross-user scans).

NOTE: this migration emits its policy DDL as LITERAL SQL rather than importing
app.db.rls.users_isolation_statements(). That helper is the single source for the
*current* policy shape and evolves (migration 036 adds an external_subject_id
read-bootstrap clause referencing a column that does not exist at 029). A
migration must reproduce the state it introduced, so 029 is frozen to the
id/email policy it originally created; later shape changes ship as their own
revisions.
"""

from alembic import op

revision = "029"
down_revision = "028"

_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_EMAIL = "NULLIF(current_setting('app.current_login_email', true), '')"


def upgrade() -> None:
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY users_isolation ON users "
        f"USING (id = {_UID} OR email = {_EMAIL}) "
        f"WITH CHECK (id = {_UID})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS users_isolation ON users")
    op.execute("ALTER TABLE users NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
