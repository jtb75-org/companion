import uuid

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import MemoryCategory, MemorySource


class FunctionalMemory(TimestampMixin, Base):
    __tablename__ = "functional_memory"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "category", "key",
            name="uq_functional_memory_user_category_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[MemoryCategory] = mapped_column(nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    # Encrypted at rest (per-tenant envelope) — tagged-ciphertext Text
    # holding encrypted JSON. Read via field_crypto.decrypt_row_field.
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[MemorySource] = mapped_column(nullable=False)

    # Relationships
    user = relationship("User", back_populates="functional_memories")
