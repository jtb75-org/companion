import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Medication(TimestampMixin, Base):
    __tablename__ = "medications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    dosage: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    schedule: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pharmacy: Mapped[str | None] = mapped_column(Text, nullable=True)
    prescriber: Mapped[str | None] = mapped_column(Text, nullable=True)
    refill_due_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    # Relationships
    user = relationship("User", back_populates="medications")
    confirmations = relationship(
        "MedicationConfirmation",
        back_populates="medication",
        cascade="all, delete-orphan",
    )


class MedicationConfirmation(Base):
    __tablename__ = "medication_confirmations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    medication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("medications.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalized from medications.user_id for per-user RLS; kept consistent by
    # the enforce_same_user trigger. NOT NULL as of Phase 2e (migration 027).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    scheduled_at: Mapped[datetime] = mapped_column(nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    missed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    # Relationships
    medication = relationship("Medication", back_populates="confirmations")
