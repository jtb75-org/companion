import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import AccessTier, RelationshipType


class TrustedContact(Base):
    __tablename__ = "trusted_contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    contact_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stable Authentik OIDC subject for the caregiver PERSON, lazy-backfilled at
    # BFF login (app/api/auth_authentik.py, active only when auth_provider ==
    # "authentik"). Lets a caregiver session (which carries only the opaque sub)
    # recover the verified email without storing PII in Redis. NON-unique: one
    # caregiver may serve several members, so their N contact rows share one sub.
    external_subject_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    relationship_type: Mapped[RelationshipType] = mapped_column(nullable=False)
    access_tier: Mapped[AccessTier] = mapped_column(
        nullable=False, default=AccessTier.TIER_1
    )
    tier_3_scope: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    added_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    last_viewed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Invitation tracking
    invitation_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="accepted"
    )
    invitation_token: Mapped[str | None] = mapped_column(
        Text, unique=True, nullable=True
    )
    invited_at: Mapped[datetime | None] = mapped_column(nullable=True)
    invited_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id"), nullable=True
    )
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="trusted_contacts")
    activity_logs = relationship(
        "CaregiverActivityLog",
        back_populates="trusted_contact",
        cascade="all, delete-orphan",
    )
