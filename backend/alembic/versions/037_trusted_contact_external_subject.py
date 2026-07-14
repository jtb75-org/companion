"""Add trusted_contacts.external_subject_id (caregiver OIDC subject → email)

Revision ID: 037
Revises: 036

PR #1 of the caregiver Firebase->Authentik wave — ADDITIVE AND INERT. Adds the
stable Authentik OIDC ``sub`` for the caregiver PERSON. It is lazy-backfilled at
BFF login (app/api/auth_authentik.py, active only when auth_provider=="authentik")
and lets a caregiver session — which stores only the opaque ``sub`` in Redis, no
PII — recover the caregiver's verified email at request time to run the existing
``caregiver_authorized_for_member(email, user_id)`` gate.

Nullable: the Firebase path never sets it, so existing rows stay NULL and nothing
changes for the live caregiver auth. Unlike ``users.external_subject_id`` (035)
this is NOT unique: one caregiver may serve several members, so their N contact
rows share a single subject. A plain (non-unique) index serves the by-sub lookup.

trusted_contacts is under per-member RLS (030); this column is not part of any
policy and the caregiver-context reads that use it run on the maintenance
(BYPASSRLS) session, so no policy change is needed here.
"""

import sqlalchemy as sa

from alembic import op

revision = "037"
down_revision = "036"


def upgrade() -> None:
    op.add_column(
        "trusted_contacts",
        sa.Column("external_subject_id", sa.Text(), nullable=True),
    )
    # NON-unique: many rows may share one caregiver subject. Serves the by-sub
    # lookup in the Authentik caregiver resolver.
    op.create_index(
        "ix_trusted_contacts_external_subject_id",
        "trusted_contacts",
        ["external_subject_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trusted_contacts_external_subject_id", table_name="trusted_contacts"
    )
    op.drop_column("trusted_contacts", "external_subject_id")
