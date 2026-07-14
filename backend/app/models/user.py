import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, Text, Time, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # email/first_name/last_name/display_name/preferred_name are intentionally
    # PLAINTEXT: email is the unique identity key; the names drive auth gates
    # (require_complete_profile), display, and lookups. Encrypting them would
    # break those. phone/date_of_birth/address ARE encrypted at rest
    # (per-tenant envelope) and so are stored as tagged-ciphertext Text.
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # Stable OIDC subject (Authentik per-provider ``sub``) → member mapping. NULL
    # for Firebase-era rows; backfilled on first Authentik login (see
    # app/api/auth_authentik.py). Nullable + UNIQUE: Postgres permits many NULLs,
    # so it is additive/inert until the Authentik cutover.
    external_subject_id: Mapped[str | None] = mapped_column(
        Text, unique=True, nullable=True
    )
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_of_birth: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    primary_language: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="en"
    )

    # D.D. personality preferences
    voice_id: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="arlo_default"
    )
    pace_setting: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="normal"
    )
    warmth_level: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="warm"
    )
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Quiet hours
    quiet_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    checkin_time: Mapped[time | None] = mapped_column(
        Time, server_default=text("'09:00'")
    )

    # Away mode
    away_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    away_expires_at: Mapped[datetime | None] = mapped_column(
        nullable=True
    )

    # Care model & account status
    care_model: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="self_directed"
    )
    account_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active"
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deletion_scheduled_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    trusted_contacts = relationship(
        "TrustedContact", back_populates="user", cascade="all, delete-orphan",
        foreign_keys="[TrustedContact.user_id]",
    )
    documents = relationship(
        "Document", back_populates="user", cascade="all, delete-orphan"
    )
    medications = relationship(
        "Medication", back_populates="user", cascade="all, delete-orphan"
    )
    appointments = relationship(
        "Appointment", back_populates="user", cascade="all, delete-orphan"
    )
    bills = relationship(
        "Bill", back_populates="user", cascade="all, delete-orphan"
    )
    todos = relationship(
        "Todo", back_populates="user", cascade="all, delete-orphan"
    )
    questions = relationship(
        "QuestionTracker", back_populates="user", cascade="all, delete-orphan"
    )
    functional_memories = relationship(
        "FunctionalMemory", back_populates="user", cascade="all, delete-orphan"
    )
    caregiver_activity_logs = relationship(
        "CaregiverActivityLog",
        back_populates="user",
        cascade="all, delete-orphan",
        # Defer child deletion to the DB ON DELETE CASCADE (FK on caregiver_activity_log),
        # which runs as the table OWNER — so deleting a user does NOT emit an ORM
        # DELETE on the append-only log as companion_app (which lacks DELETE). Without
        # this, the grace=0 member self-serve deletion (runs as companion_app) would 500
        # on permission-denied. See app/db/grants.py.
        passive_deletes=True,
    )
