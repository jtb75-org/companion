import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import CaregiverAction, DeletionReason


class CaregiverActivityLog(Base):
    __tablename__ = "caregiver_activity_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # SET NULL (not CASCADE): revoking/deleting a caregiver RETAINS their activity
    # history — the row keeps user_id + action + occurred_at, only the contact link is
    # nulled — so Sam can still view the full log (docs §5). Nullable only for that
    # post-revocation state; every INSERT sets it (log_caregiver_action).
    trusted_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trusted_contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[CaregiverAction] = mapped_column(nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    trusted_contact = relationship("TrustedContact", back_populates="activity_logs")
    user = relationship("User", back_populates="caregiver_activity_logs")


class AccountAuditLog(Base):
    """Account/auth access-control events (e.g. activation, refused signup)."""

    __tablename__ = "account_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable + no FK: a refused signup has no user row.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )


class DeletionAuditLog(Base):
    __tablename__ = "deletion_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )  # No FK: user row may itself be deleted
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[DeletionReason] = mapped_column(nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
