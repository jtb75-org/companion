"""Enable per-user RLS on trusted_contacts (WS1 Phase 2e — bootstrap table)

Revision ID: 030
Revises: 029

trusted_contacts.user_id is the MEMBER/owner (the caregiver is stored only as
contact_email, not a user_id). So the STANDARD flat per-user policy fits — no
bespoke clause. The bootstrap paths that read/write a contact before any member
GUC exists (caregiver token auth, web caregiver authz, invitation token
accept/decline/validate, the caregiver-portal authz check, and the cross-member
deletion cleanup) were routed through the maintenance (BYPASSRLS) session in the
same PR, so no read-bootstrap OR-clause is needed. Deliberately NOT adding an
`app.current_contact_email` OR-clause: it would widen every companion_app read to
"all rows with this email across all members", the leak kali flagged. The token
capability + narrow maintenance reads cover the legitimate cross-member cases.
The WRITE fence stays user_id = current_user_id — a caregiver session can never
write a member's contact row.
"""

from alembic import op
from app.db.rls import drop_isolation_statements, tenant_isolation_statements

revision = "030"
down_revision = "029"


def upgrade() -> None:
    for stmt in tenant_isolation_statements("trusted_contacts"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in drop_isolation_statements("trusted_contacts"):
        op.execute(stmt)
