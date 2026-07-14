"""Add activation_tokens (email-keyed branded account-activation capability)

Revision ID: 040
Revises: 039

Generic, cohort-agnostic activation for the branded Authentik set-password flow: an
account is created (an admin now via admin_users, members later via users) and an
email-keyed, single-use token lets that person set their Authentik password in
Companion's UI before their first login. Keyed by ``email`` (NOT admin/member id) so
the same table + service serve any cohort.

NOT per-member data — a token is consulted BEFORE any authenticated session / tenant
GUC exists (public /activation/validate + set-password), so this table is accessed on
a plain / maintenance (BYPASSRLS) session and is intentionally left OUT of per-user
RLS (mirrors account_audit_log 020, which likewise carries a pre-auth email and no
RLS policy). ``token`` is UNIQUE (the capability); ``email`` is indexed but NON-unique
so a re-issue supersedes a prior unused token.

Reversible: downgrade drops the indexes + table cleanly (no data migration — these are
short-lived pre-auth capabilities).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "040"
down_revision = "039"


def upgrade() -> None:
    op.create_table(
        "activation_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Non-unique: many rows per email over time (re-issues); serves the by-email scan.
    op.create_index("ix_activation_tokens_email", "activation_tokens", ["email"])
    # Unique: the token is the lookup capability on every validate / set-password.
    op.create_index(
        "ix_activation_tokens_token", "activation_tokens", ["token"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_activation_tokens_token", table_name="activation_tokens")
    op.drop_index("ix_activation_tokens_email", table_name="activation_tokens")
    op.drop_table("activation_tokens")
