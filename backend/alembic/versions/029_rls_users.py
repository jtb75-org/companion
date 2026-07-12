"""Enable per-user RLS on the users table (WS1 Phase 2e — bootstrap table)

Revision ID: 029
Revises: 028

The auth-critical table, enabled last. `users` gets the dual-clause policy
(app/db/rls.py:users_isolation_statements): a row is READABLE when its id
matches app.current_user_id OR its email matches app.current_login_email (the
pre-user-id auth-by-email bootstrap), but WRITES are fenced to the tenant GUC
(id = app.current_user_id) so the email bootstrap can never authorize a write.

Prerequisites landed in #57 (GUC/maintenance wiring) and the follow-up
(profile.py DEK GUC, invitations.py maintenance reads). An access-path audit
confirmed every users read/write is either GUC-bootstrapped on the request
session (auth deps, profile, complete-profile) or runs under the BYPASSRLS
companion_maintenance role (admin, invitation stubs, workers' cross-user scans).
"""

from alembic import op
from app.db.rls import drop_users_isolation_statements, users_isolation_statements

revision = "029"
down_revision = "028"


def upgrade() -> None:
    for stmt in users_isolation_statements():
        op.execute(stmt)


def downgrade() -> None:
    for stmt in drop_users_isolation_statements():
        op.execute(stmt)
