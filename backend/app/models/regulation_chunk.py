import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RegulationChunk(Base):
    __tablename__ = "disability_reg_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    jurisdiction: Mapped[str] = mapped_column(
        String(50), nullable=False, default="US_Federal"
    )
    source_corpus: Mapped[str] = mapped_column(
        String(50), nullable=False  # "eCFR" or "Federal_Register"
    )
    source_url: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    citation: Mapped[str] = mapped_column(
        Text, nullable=False  # "20 CFR § 404.1520"
    )
    title: Mapped[str] = mapped_column(
        String(20), nullable=True  # "20"
    )
    part: Mapped[str] = mapped_column(
        String(20), nullable=True  # "404"
    )
    section: Mapped[str] = mapped_column(
        String(20), nullable=True  # "1520"
    )
    program: Mapped[str] = mapped_column(
        String(20), nullable=False  # "SSDI", "SSI", "Both"
    )
    text_content: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    token_count: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    effective_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retrieval_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    embedding = mapped_column(Vector(768), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
