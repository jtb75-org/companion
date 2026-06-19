import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import (
    DocumentClassification,
    DocumentStatus,
    RetentionPhase,
    RoutingDestination,
    SourceChannel,
    UrgencyLevel,
)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_channel: Mapped[SourceChannel] = mapped_column(nullable=False)
    raw_text_ref: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[DocumentClassification | None] = mapped_column(
        nullable=True
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    urgency_level: Mapped[UrgencyLevel | None] = mapped_column(nullable=True)
    # Encrypted at rest (per-tenant envelope, app.services.field_crypto).
    # Stored as tagged-ciphertext Text; extracted_fields holds encrypted JSON.
    extracted_fields: Mapped[str | None] = mapped_column(Text, nullable=True)
    spoken_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    reading_grade: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 1), nullable=True
    )
    routing_destination: Mapped[RoutingDestination | None] = mapped_column(
        nullable=True
    )
    status: Mapped[DocumentStatus] = mapped_column(
        nullable=False, default=DocumentStatus.RECEIVED
    )
    source_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    page_count: Mapped[int | None] = mapped_column(nullable=True, server_default="1")
    retention_phase: Mapped[RetentionPhase] = mapped_column(
        nullable=False, default=RetentionPhase.FULL
    )

    # Relationships
    user = relationship("User", back_populates="documents")
    appointments = relationship("Appointment", back_populates="source_document")
    bills = relationship("Bill", back_populates="source_document")
    chunks = relationship(
        "DocumentChunk",
        back_populates="document",
        cascade="all, delete-orphan",
    )
    pipeline_metrics = relationship("PipelineMetric", back_populates="document")
