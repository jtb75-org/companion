"""caregiver_activity_log.trusted_contact_id: ON DELETE CASCADE -> SET NULL

Revision ID: 039
Revises: 038

Retain caregiver activity history when a trusted_contact is deleted (a revoked
caregiver): instead of CASCADE-erasing the log rows, the FK now SET NULLs the contact
link, so the row keeps user_id + action + occurred_at and Sam can still view the full
log (docs/caregiver-access-and-privacy.md §5). Owner decision 2026-07-14.

The column becomes nullable (only for that post-revocation state; every INSERT still
sets it). The user_id FK is intentionally left ON DELETE CASCADE — a MEMBER's own
deletion (right-to-erasure) still removes their audit rows. Pairs with passive_deletes
on the ORM relationships so companion_app (which lacks UPDATE on the append-only table,
PR #83) never emits an ORM UPDATE — the DB SET NULL, run as the table owner, does it.
TrustedContact.activity_logs uses passive_deletes="all" (True alone still nulls a LOADED
collection's FKs via an ORM UPDATE); User.caregiver_activity_logs stays True (its
user_id FK is ON DELETE CASCADE, which does not FK-null).
"""

from sqlalchemy.dialects.postgresql import UUID

from alembic import op
from app.db.rls_migration import rls_bypassed

revision = "039"
down_revision = "038"

_FK = "caregiver_activity_log_trusted_contact_id_fkey"


def upgrade() -> None:
    op.alter_column(
        "caregiver_activity_log",
        "trusted_contact_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )
    op.drop_constraint(_FK, "caregiver_activity_log", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "caregiver_activity_log",
        "trusted_contacts",
        ["trusted_contact_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Retained NULL-contact rows (a revoked caregiver's history) CANNOT be represented
    # by the restored NOT NULL + CASCADE schema. Downgrade explicitly DISCARDS that
    # history so the revert is executable regardless of data state (they'd have been
    # CASCADE-erased under the old schema anyway).
    # caregiver_activity_log is under FORCE ROW LEVEL SECURITY (migration 028);
    # this owner-run DELETE sets no app.current_user_id GUC, so without the
    # bypass it would silently match ZERO rows and the SET NOT NULL below would
    # then fail on any retained NULL-contact row. rls_bypassed makes the discard
    # actually happen so the revert is executable regardless of data state.
    with rls_bypassed(op, "caregiver_activity_log"):
        op.execute(
            "DELETE FROM caregiver_activity_log WHERE trusted_contact_id IS NULL"
        )
    op.drop_constraint(_FK, "caregiver_activity_log", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "caregiver_activity_log",
        "trusted_contacts",
        ["trusted_contact_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "caregiver_activity_log",
        "trusted_contact_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
