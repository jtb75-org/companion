"""Pipeline orchestrator — chains all 6 stages, records metrics, and emits events."""

import logging
import time
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.publisher import event_publisher
from app.events.schemas import (
    DocumentProcessedPayload,
    DocumentRoutedPayload,
)
from app.models.document import Document
from app.models.enums import (
    DocumentClassification,
    DocumentStatus,
    RoutingDestination,
    UrgencyLevel,
)
from app.models.pipeline_metrics import PipelineMetric
from app.pipeline.classification import classify
from app.pipeline.events import (
    publish_pipeline_event as _publish_event,
)
from app.pipeline.extraction import extract
from app.pipeline.ingestion import (
    process_camera_scan,
    process_email,
)
from app.pipeline.routing import route
from app.pipeline.schemas import PipelineResult
from app.pipeline.summarization import summarize
from app.pipeline.tracker import create_questions

logger = logging.getLogger(__name__)


async def process_document(
    db: AsyncSession, document_id: UUID, user_id: UUID
) -> PipelineResult:
    """Run the full 6-stage pipeline on a document."""

    pipeline_start = time.monotonic()
    doc = await db.get(Document, document_id)
    if not doc:
        raise ValueError(f"Document {document_id} not found")

    # Wrapper that always includes user_id
    async def publish_pipeline_event(
        doc_id, stage, status, metadata=None,
    ):
        await _publish_event(
            doc_id, stage, status,
            metadata=metadata, user_id=str(user_id),
        )

    # Update status to processing
    doc.status = DocumentStatus.PROCESSING
    await db.flush()

    try:
        # Stage 1: Ingestion
        stage_start = time.monotonic()
        await publish_pipeline_event(
            document_id, "ingestion", "started",
        )
        source = getattr(
            doc.source_channel, "value", str(doc.source_channel)
        )
        if source == "camera_scan":
            normalized = await process_camera_scan(db, document_id)
        else:
            normalized = await process_email(db, document_id)
        await _record_metric(db, document_id, "ingestion", "completed", stage_start)
        await publish_pipeline_event(
            document_id, "ingestion", "completed",
        )

        # Stage 2: Classification
        stage_start = time.monotonic()
        await publish_pipeline_event(
            document_id, "classification", "started",
        )
        classification_result = await classify(normalized)

        # Map string values to enums for the Document model
        doc.classification = DocumentClassification(classification_result.classification)
        doc.confidence_score = classification_result.confidence_score
        doc.urgency_level = UrgencyLevel(classification_result.urgency_level)
        doc.status = DocumentStatus.CLASSIFIED
        await db.flush()
        await _record_metric(db, document_id, "classification", "completed", stage_start, {
            "classification": classification_result.classification,
            "confidence": classification_result.confidence_score,
            "tier": classification_result.classifier_tier,
        })
        await publish_pipeline_event(
            document_id, "classification", "completed",
            {
                "classification": (
                    classification_result.classification
                ),
                "confidence": (
                    classification_result.confidence_score
                ),
            },
        )

        # Stage 3: Extraction
        stage_start = time.monotonic()
        await publish_pipeline_event(
            document_id, "extraction", "started",
        )
        extraction_result = await extract(normalized, classification_result, db)
        # Encrypt at rest (per-tenant envelope). Stored as tagged-ciphertext
        # Text holding the JSON of extracted_fields.
        from app.services.field_crypto import encrypt_json_for_user
        doc.extracted_fields = await encrypt_json_for_user(
            db, user_id, extraction_result.extracted_fields
        )
        await db.flush()
        await _record_metric(db, document_id, "extraction", "completed", stage_start, {
            "missing_fields": extraction_result.missing_fields,
        })
        await publish_pipeline_event(
            document_id, "extraction", "completed",
        )

        # Stage 4: Summarization
        stage_start = time.monotonic()
        await publish_pipeline_event(
            document_id, "summarization", "started",
        )
        summarization_result = await summarize(classification_result, extraction_result, db)
        from app.services.field_crypto import encrypt_for_user
        doc.spoken_summary = await encrypt_for_user(
            db, user_id, summarization_result.spoken_summary
        )
        doc.card_summary = await encrypt_for_user(
            db, user_id, summarization_result.card_summary
        )
        doc.reading_grade = Decimal(str(summarization_result.reading_grade))
        doc.status = DocumentStatus.SUMMARIZED
        await db.flush()
        await _record_metric(db, document_id, "summarization", "completed", stage_start)
        await publish_pipeline_event(
            document_id, "summarization", "completed",
        )

        # Stage 4.5: Embedding (non-blocking)
        try:
            stage_start = time.monotonic()
            await publish_pipeline_event(
                document_id, "embedding", "started",
            )
            from app.pipeline.embeddings import embed_document
            chunk_count = await embed_document(
                db, document_id, user_id,
                classification_result,
                extraction_result,
                summarization_result,
            )
            await _record_metric(
                db, document_id, "embedding", "completed",
                stage_start, {"chunks": chunk_count},
            )
            await publish_pipeline_event(
                document_id, "embedding", "completed",
                {"chunks": chunk_count},
            )
        except Exception as emb_err:
            logger.warning(
                "Embedding failed for doc %s: %s",
                document_id, emb_err,
            )
            await _record_metric(
                db, document_id, "embedding", "failed",
                stage_start, {"error": str(emb_err)},
            )
            await publish_pipeline_event(
                document_id, "embedding", "failed",
                {"error": str(emb_err)},
            )

        # Stage 5: Routing
        stage_start = time.monotonic()
        await publish_pipeline_event(
            document_id, "routing", "started",
        )
        # Fetch user's care model and source channel
        from app.models.user import User
        user = await db.get(User, user_id)
        care_model = (
            getattr(user, "care_model", "self_directed")
            if user else "self_directed"
        )
        source_ch = getattr(
            doc.source_channel, "value",
            str(doc.source_channel),
        )

        routing_result = await route(
            db, user_id, classification_result,
            extraction_result, summarization_result,
            care_model=care_model,
            source_channel=source_ch,
        )
        doc.routing_destination = RoutingDestination(
            routing_result.routing_destination
        )
        if routing_result.pending_review_id:
            doc.status = DocumentStatus.PENDING_REVIEW
        else:
            doc.status = DocumentStatus.ROUTED
        await db.flush()
        await _record_metric(db, document_id, "routing", "completed", stage_start, {
            "destination": routing_result.routing_destination,
            "records_created": len(routing_result.records_created),
        })
        await publish_pipeline_event(
            document_id, "routing", "completed",
            {
                "destination": (
                    routing_result.routing_destination
                ),
            },
        )

        # Stage 6: Question Tracker
        stage_start = time.monotonic()
        questions = await create_questions(
            db, user_id, classification_result, extraction_result
        )
        await _record_metric(db, document_id, "tracking", "completed", stage_start, {
            "questions_created": len(questions),
        })

        # Calculate total processing time
        total_ms = int((time.monotonic() - pipeline_start) * 1000)
        doc.processed_at = datetime.utcnow()
        await db.flush()

        # Emit events
        await event_publisher.publish(
            "document.processed",
            user_id=user_id,
            payload=DocumentProcessedPayload(
                document_id=document_id,
                classification=classification_result.classification,
                confidence_score=classification_result.confidence_score,
                urgency_level=classification_result.urgency_level,
                extracted_fields=extraction_result.extracted_fields,
            ),
        )

        await event_publisher.publish(
            "document.routed",
            user_id=user_id,
            payload=DocumentRoutedPayload(
                document_id=document_id,
                routing_destination=routing_result.routing_destination,
                card_summary=summarization_result.card_summary,
                spoken_summary=summarization_result.spoken_summary,
            ),
        )

        logger.info(
            "Pipeline complete: doc=%s class=%s conf=%.2f route=%s time=%dms",
            document_id,
            classification_result.classification,
            classification_result.confidence_score,
            routing_result.routing_destination,
            total_ms,
        )

        return PipelineResult(
            document_id=document_id,
            classification=classification_result,
            extraction=extraction_result,
            summarization=summarization_result,
            routing=routing_result,
            processing_time_ms=total_ms,
        )

    except Exception as e:
        logger.exception(
            "Pipeline failed for document %s", document_id
        )
        await _record_metric(
            db, document_id, "pipeline", "failed",
            pipeline_start, {"error": str(e)},
        )
        await publish_pipeline_event(
            document_id, "pipeline", "failed",
            {"error": str(e)},
        )
        raise


async def _record_metric(
    db: AsyncSession,
    document_id: UUID,
    stage: str,
    status: str,
    start_time: float,
    metadata: dict | None = None,
) -> None:
    """Record a pipeline stage metric."""
    duration_ms = int((time.monotonic() - start_time) * 1000)
    metric = PipelineMetric(
        document_id=document_id,
        stage=stage,
        status=status,
        duration_ms=duration_ms,
        stage_metadata=metadata,
    )
    db.add(metric)
    await db.flush()
