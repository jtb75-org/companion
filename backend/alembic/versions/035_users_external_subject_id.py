"""Add users.external_subject_id (stable OIDC subject → member mapping)

Revision ID: 035
Revises: 034

PR #3 of the Firebase->Authentik migration — ADDITIVE AND INERT. Adds the stable
OIDC ``sub`` column that replaces email-matching in the Authentik BFF login path
(app/api/auth_authentik.py, active only when auth_provider=="authentik").

Nullable: the Firebase path never sets it, so existing rows stay NULL and nothing
changes for the live auth. UNIQUE via a unique index — in Postgres a UNIQUE index
permits multiple NULLs, so many not-yet-backfilled members coexist while any two
non-null subjects must differ. The index also serves the by-sub lookup.

This is the RLS-bootstrap column the users policy (029) anticipated ("swapped for
the OIDC subject when Authentik lands"); the RLS policy is NOT changed here.
"""

import sqlalchemy as sa

from alembic import op

revision = "035"
down_revision = "034"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("external_subject_id", sa.Text(), nullable=True),
    )
    # UNIQUE index doubles as the by-sub lookup index; nullable → many NULLs OK.
    op.create_index(
        "uq_users_external_subject_id",
        "users",
        ["external_subject_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_users_external_subject_id", table_name="users")
    op.drop_column("users", "external_subject_id")
