"""Add admin_users.external_subject_id (admin OIDC subject → admin row)

Revision ID: 038
Revises: 037

PR #1 of the admin Firebase->Authentik wave — ADDITIVE AND INERT. Adds the stable
Authentik OIDC ``sub`` for an admin, lazy-backfilled at BFF login (a later PR, active
only when auth_provider=="authentik"). It lets an admin session — which stores only
the opaque ``sub`` in Redis, no PII — resolve to the ``admin_users`` row WITHOUT a
``users`` row (admins are not members), so a pure admin can authenticate under
Authentik just like members/caregivers do.

Nullable: the Firebase path never sets it, so existing rows stay NULL and nothing
changes for the live admin auth. UNIQUE via a unique index (like users 035, unlike
the caregiver 037 non-unique column): one admin ↔ one row ↔ one subject; a Postgres
UNIQUE index permits many NULLs so un-backfilled admins coexist. The index also serves
the by-subject lookup. ``admin_users`` is RLS-disabled, so no policy is involved.
"""

import sqlalchemy as sa

from alembic import op

revision = "038"
down_revision = "037"


def upgrade() -> None:
    op.add_column(
        "admin_users",
        sa.Column("external_subject_id", sa.Text(), nullable=True),
    )
    # UNIQUE index doubles as the by-subject lookup; nullable → many NULLs OK.
    op.create_index(
        "uq_admin_users_external_subject_id",
        "admin_users",
        ["external_subject_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_admin_users_external_subject_id", table_name="admin_users"
    )
    op.drop_column("admin_users", "external_subject_id")
