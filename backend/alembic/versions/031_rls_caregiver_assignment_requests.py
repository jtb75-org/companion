"""Enable per-user RLS on caregiver_assignment_requests (WS1 Phase 2e — final)

Revision ID: 031
Revises: 030

The last WS1 bootstrap table, and the cleanest: member_id is the member-scoping
column and is NOT NULL (no invisible-row hazard), and there is NO token column —
so no read-bootstrap clause is even conceivable. The standard flat policy on
member_id fits: a member sees/writes only the assignment requests targeting them.

An access-path audit found ZERO blockers: every member path already runs on the
request session with app.current_user_id = user.id and filters/writes
member_id == user.id; every admin/cross-member path (create, admin-approve,
list-all, delete) already runs on get_maintenance_db (BYPASSRLS); the cross-member
deletion cleanup (execute_deletion, by caregiver_email) is already on
maintenance_session (#59). No app-code change is needed alongside this migration.

Deliberately NOT a caregiver_email OR-clause: it would widen every companion_app
read to all requests for that caregiver across all members (the leak kali
flagged). The WRITE fence stays member_id = current_user_id.
"""

from alembic import op
from app.db.rls import drop_isolation_statements, tenant_isolation_statements

revision = "031"
down_revision = "030"

_TABLE = "caregiver_assignment_requests"


def upgrade() -> None:
    for stmt in tenant_isolation_statements(_TABLE, user_col="member_id"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in drop_isolation_statements(_TABLE):
        op.execute(stmt)
