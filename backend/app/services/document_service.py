from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.publisher import event_publisher
from app.events.schemas import DocumentReceivedPayload
from app.models.document import Document
from app.models.pending_review import PendingReview


async def list_documents(
    db: AsyncSession,
    user_id: UUID,
    status: str | None = None,
    classification: str | None = None,
    urgency: str | None = None,
) -> list[Document]:
    stmt = select(Document).where(Document.user_id == user_id)
    if status is not None:
        stmt = stmt.where(Document.status == status)
    if classification is not None:
        stmt = stmt.where(Document.classification == classification)
    if urgency is not None:
        stmt = stmt.where(Document.urgency_level == urgency)
    stmt = stmt.order_by(Document.received_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_document(
    db: AsyncSession, user_id: UUID, document_id: UUID
) -> Document | None:
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def update_document_status(
    db: AsyncSession, user_id: UUID, document_id: UUID, status: str
) -> Document | None:
    document = await get_document(db, user_id, document_id)
    if document is None:
        return None
    document.status = status
    await db.flush()
    return document


async def delete_document(
    db: AsyncSession, user_id: UUID, document_id: UUID
) -> bool:
    document = await get_document(db, user_id, document_id)
    if document is None:
        return False
    # Remove any pending reviews for this document too, so deleting it also
    # takes it out of the member's review queue. The FK is ON DELETE SET NULL,
    # which would otherwise leave a document-less review card stuck pending
    # (this is what made "Remove this one" appear not to work for duplicates).
    await db.execute(
        delete(PendingReview).where(
            PendingReview.document_id == document_id,
            PendingReview.user_id == user_id,
        )
    )
    await db.delete(document)
    await db.flush()
    return True


async def create_document(
    db: AsyncSession, user_id: UUID, data: dict
) -> Document:
    # Ensure raw_text_ref has a value (will be set properly by pipeline)
    if "raw_text_ref" not in data:
        data["raw_text_ref"] = "pending"
    # Stamp the ingest time in Python, per document. The column's DB default is
    # ``now()`` == transaction_timestamp(), which is CONSTANT for a whole
    # transaction — so two documents created in one transaction got an IDENTICAL
    # received_at down to the microsecond, and any document created inside a
    # reused/long-lived transaction inherited that transaction's (stale, past)
    # start time instead of its own upload moment. Setting a fresh wall-clock
    # value here decouples received_at from transaction boundaries so it always
    # reflects the real ingest time. ``setdefault`` still lets a caller pass an
    # explicit received_at (e.g. an email's own received date).
    data.setdefault("received_at", datetime.utcnow())
    document = Document(user_id=user_id, **data)
    db.add(document)
    await db.flush()

    await event_publisher.publish(
        "document.received",
        user_id=user_id,
        payload=DocumentReceivedPayload(
            document_id=document.id,
            source_channel=getattr(
                document.source_channel, "value",
                str(document.source_channel),
            ),
        ),
    )

    return document
