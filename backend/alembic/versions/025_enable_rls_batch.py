"""Enable per-user RLS on the main member-table batch (WS1 Phase 2e)

Revision ID: 025
Revises: 024

Applies the standard flat per-user policy (app/db/rls.py — same shape proven
live on todos and functional_memory) to the 8 batch tables cleared by the
access-path audit + niru's PR #50 review:

  documents, document_chunks, bills, appointments, medications,
  pending_reviews, questions_tracker, chat_sessions

Prerequisites (all deployed): tenant GUC in auth deps + caregiver endpoints
(+ dashboard/activity/alerts), the document-pipeline entrypoint GUC, per-user
worker GUCs, cross-user workers + the 8 cross-member admin modules on the
maintenance (BYPASSRLS) connection.

Deliberately EXCLUDED:
- device_tokens — register_token does an intentional CROSS-USER fcm_token
  reassignment (globally-unique token moving between users); flat RLS hides
  the other user's row → INSERT hits the unique constraint → registration
  breaks. Needs a scoped maintenance-helper rework first (niru, PR #50 review).
- chat_messages / medication_confirmations — denormalized user_id still
  nullable; NOT NULL tightening lands with their policies in the next slice.
- users / trusted_contacts / caregiver_assignment_requests — need bootstrap
  clauses (separate migration).
"""

from alembic import op
from app.db.rls import drop_isolation_statements, tenant_isolation_statements

revision = "025"
down_revision = "024"

BATCH_TABLES = (
    "documents",
    "document_chunks",
    "bills",
    "appointments",
    "medications",
    "pending_reviews",
    "questions_tracker",
    "chat_sessions",
)


def upgrade() -> None:
    for table in BATCH_TABLES:
        for stmt in tenant_isolation_statements(table):
            op.execute(stmt)


def downgrade() -> None:
    for table in reversed(BATCH_TABLES):
        for stmt in drop_isolation_statements(table):
            op.execute(stmt)
