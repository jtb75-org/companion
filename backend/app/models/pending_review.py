import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import RecommendedAction, ReviewStatus


class PendingReview(TimestampMixin, Base):
    __tablename__ = "pending_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, native_enum=False),
        nullable=False,
        server_default=ReviewStatus.PENDING.value,
    )
    recommended_action: Mapped[RecommendedAction] = mapped_column(
        Enum(RecommendedAction, native_enum=False),
        nullable=False,
    )
    # Encrypted at rest (per-tenant envelope) — tagged-ciphertext Text
    # holding encrypted JSON. Read via field_crypto.decrypt_row_field.
    proposed_record_data: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    source_description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="'a document'"
    )
    is_urgent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_past_due: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_duplicate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    presented_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_record_type: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    created_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Relationships
    user = relationship("User")
    document = relationship("Document")
