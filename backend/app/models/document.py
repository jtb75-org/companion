import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict
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
    # MutableDict-wrapped so in-place key assignments (e.g. the OCR block in
    # ingestion.process_camera_scan setting source_metadata["ocr_text"] on an
    # already-populated dict) mark the attribute dirty and actually persist.
    # A plain JSON/JSONB dict is NOT tracked on in-place mutation, so those
    # writes were silently dropped by flush() when the dict was non-empty.
    # ORM-level change only — no DB migration needed.
    source_metadata: Mapped[dict | None] = mapped_column(
        MutableDict.as_mutable(JSONB), nullable=True
    )
    # DB default is per-row wall clock, NOT now() (== transaction_timestamp(),
    # constant per transaction — which made two docs in one transaction share an
    # identical received_at). timezone('UTC', clock_timestamp()) returns a
    # naive-UTC wall time, matching the column type (TIMESTAMP WITHOUT TIME ZONE),
    # the app's datetime.utcnow() stamping, and retention's UTC cutoffs. Passed as
    # a text() SQL expression so it renders as an unquoted function call, not a
    # string literal. The application also stamps received_at explicitly per
    # document in create_document (migration 043).
    received_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("timezone('UTC', clock_timestamp())"),
    )
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    page_count: Mapped[int | None] = mapped_column(nullable=True, server_default="1")
    # SHA-256 (hex) of the uploaded page bytes, for exact-duplicate detection at
    # upload (a member re-submitting the same file — the "I thought it glitched"
    # double-tap). Not a secret (one-way hash); scoped per-user by RLS.
    content_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set by the pipeline (fuzzy tier) when this document's extracted fields
    # closely match an EARLIER document of the same member — a likely
    # re-photograph of the same document. NON-destructive: only a hint so the app
    # can ask the member; never auto-merged (a different bill from the same biller
    # looks similar). Points at the earlier document's id.
    possible_duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
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
        # The FK is ON DELETE CASCADE, so let the DB remove chunks on document
        # delete instead of SQLAlchemy loading them first (which needlessly
        # SELECTs the pgvector `embedding` column).
        passive_deletes=True,
    )
    pipeline_metrics = relationship("PipelineMetric", back_populates="document")
