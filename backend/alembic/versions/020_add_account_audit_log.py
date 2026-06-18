"""Add account_audit_log (account/auth access-control events)

Revision ID: 020
Revises: 019
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "020"
down_revision = "019"


def upgrade() -> None:
    op.create_table(
        "account_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("details", JSONB(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_account_audit_log_email", "account_audit_log", ["email"]
    )
    op.create_index(
        "ix_account_audit_log_event", "account_audit_log", ["event"]
    )


def downgrade() -> None:
    op.drop_index("ix_account_audit_log_event", "account_audit_log")
    op.drop_index("ix_account_audit_log_email", "account_audit_log")
    op.drop_table("account_audit_log")
