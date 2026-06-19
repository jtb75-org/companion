"""Admin API — Document management and pipeline tracking."""

import logging
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser, require_admin_role
from app.db import get_db
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.models.pipeline_metrics import PipelineMetric
from app.models.user import User
from app.services.field_crypto import decrypt_row_field, decrypt_value

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/documents",
    tags=["Admin - Documents"],
)

_editor = require_admin_role("editor")


@router.get("")
async def list_documents(
    status: DocumentStatus | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_db),
):
    """List all documents with pagination."""
    base = (
        select(
            Document.id,
            User.preferred_name.label("user_name"),
            User.email.label("user_email"),
            Document.source_channel,
            Document.status,
            Document.classification,
            Document.urgency_level,
            Document.user_id,
            Document.card_summary,
            Document.received_at,
            Document.processed_at,
        )
        .join(User, Document.user_id == User.id)
        .order_by(Document.received_at.desc())
    )
    count_q = select(func.count()).select_from(Document)

    if status is not None:
        base = base.where(Document.status == status)
        count_q = count_q.where(Document.status == status)

    result = await db.execute(
        base.limit(limit).offset(offset)
    )
    rows = result.all()
    total = await db.scalar(count_q)

    # Collect document IDs for pipeline metrics query
    doc_ids = [row.id for row in rows]

    # Fetch pipeline metrics for all documents
    stage_map: dict[str, list] = {}
    if doc_ids:
        metrics_q = await db.execute(
            select(PipelineMetric)
            .where(PipelineMetric.document_id.in_(doc_ids))
            .order_by(PipelineMetric.recorded_at)
        )
        for m in metrics_q.scalars().all():
            did = str(m.document_id)
            if did not in stage_map:
                stage_map[did] = []
            stage_map[did].append({
                "stage": m.stage.capitalize(),
                "status": "completed" if m.status == "completed" else "failed",
                "duration_ms": m.duration_ms,
            })

    items = []
    for row in rows:
        did = str(row.id)
        items.append({
            "id": did,
            "user_name": row.user_name,
            "user_email": row.user_email,
            "source_channel": (
                row.source_channel.value
                if row.source_channel
                else None
            ),
            "status": (
                row.status.value
                if row.status
                else None
            ),
            "classification": (
                row.classification.value
                if row.classification
                else None
            ),
            "urgency_level": (
                row.urgency_level.value
                if row.urgency_level
                else None
            ),
            "card_summary": await decrypt_value(
                db, row.user_id, row.card_summary
            ),
            "created_at": (
                row.received_at.isoformat()
                if row.received_at
                else None
            ),
            "processed_at": (
                row.processed_at.isoformat()
                if row.processed_at
                else None
            ),
            "pipeline_stages": stage_map.get(did, []),
        })

    return {
        "documents": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def _decrypt_ocr_shadow(db: AsyncSession, doc: Document) -> dict | None:
    """Return the OCR shadow A/B comparison with shadow_text decrypted."""
    shadow = (doc.source_metadata or {}).get("ocr_shadow")
    if not isinstance(shadow, dict):
        return None
    out = dict(shadow)
    out["shadow_text"] = await decrypt_value(
        db, doc.user_id, shadow.get("shadow_text")
    )
    return out


@router.get("/{document_id}")
async def get_document_detail(
    document_id: uuid.UUID,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_db),
):
    """Get full document details including OCR text, extracted fields, and pipeline stages."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Get user info
    user = await db.get(User, doc.user_id)

    # Get pipeline metrics
    metrics_q = await db.execute(
        select(PipelineMetric)
        .where(PipelineMetric.document_id == document_id)
        .order_by(PipelineMetric.recorded_at)
    )
    stages = [
        {
            "stage": m.stage.capitalize(),
            "status": m.status,
            "duration_ms": m.duration_ms,
            "error_message": m.error_message,
            "metadata": m.stage_metadata,
            "recorded_at": m.recorded_at.isoformat() if m.recorded_at else None,
        }
        for m in metrics_q.scalars().all()
    ]

    return {
        "id": str(doc.id),
        "user_name": user.preferred_name if user else None,
        "user_email": user.email if user else None,
        "source_channel": doc.source_channel.value if doc.source_channel else None,
        "status": doc.status.value if doc.status else None,
        "classification": doc.classification.value if doc.classification else None,
        "urgency_level": doc.urgency_level.value if doc.urgency_level else None,
        "confidence_score": float(doc.confidence_score) if doc.confidence_score else None,
        "card_summary": await decrypt_row_field(db, doc, "card_summary"),
        "spoken_summary": await decrypt_row_field(db, doc, "spoken_summary"),
        "extracted_fields": await decrypt_row_field(db, doc, "extracted_fields"),
        "routing_destination": doc.routing_destination.value if doc.routing_destination else None,
        "page_count": doc.page_count,
        # ocr_text + ocr_shadow.shadow_text are encrypted PHI at rest.
        "ocr_text": await decrypt_value(
            db, doc.user_id, (doc.source_metadata or {}).get("ocr_text")
        ),
        "ocr_shadow": await _decrypt_ocr_shadow(db, doc),
        "source_metadata": {
            k: v for k, v in (doc.source_metadata or {}).items()
            if k not in ("ocr_text", "ocr_shadow")
        },
        "created_at": doc.received_at.isoformat() if doc.received_at else None,
        "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
        "pipeline_stages": stages,
    }


@router.post("/{document_id}/cancel")
async def cancel_document(
    document_id: uuid.UUID,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a document by setting its status to FAILED."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=404, detail="Document not found"
        )
    doc.status = DocumentStatus.FAILED
    await db.commit()
    logger.info(
        "Document %s cancelled by admin %s",
        document_id,
        admin.email,
    )
    return {"document_id": str(document_id), "status": "failed"}


@router.post("/{document_id}/resubmit")
async def resubmit_document(
    document_id: uuid.UUID,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_db),
):
    """Reset a document to RECEIVED and re-trigger the pipeline."""
    from app.events.publisher import event_publisher
    from app.events.schemas import DocumentReceivedPayload

    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=404, detail="Document not found"
        )
    # Clear previous pipeline metrics and pending reviews
    await db.execute(
        delete(PipelineMetric).where(
            PipelineMetric.document_id == document_id
        )
    )
    from app.models.pending_review import PendingReview
    await db.execute(
        delete(PendingReview).where(
            PendingReview.document_id == document_id
        )
    )
    doc.status = DocumentStatus.RECEIVED
    doc.classification = None
    doc.confidence_score = None
    doc.urgency_level = None
    doc.extracted_fields = None
    doc.spoken_summary = None
    doc.card_summary = None
    doc.routing_destination = None
    doc.processed_at = None
    
    # Save resets
    await db.commit()

    # Trigger pipeline via event
    await event_publisher.publish(
        "document.received",
        user_id=doc.user_id,
        payload=DocumentReceivedPayload(
            document_id=doc.id,
            source_channel=getattr(
                doc.source_channel, "value",
                str(doc.source_channel),
            ),
        ),
    )

    logger.info(
        "Document %s resubmitted by admin %s",
        document_id,
        admin.email,
    )
    return {
        "document_id": str(document_id),
        "status": "resubmitted",
    }
