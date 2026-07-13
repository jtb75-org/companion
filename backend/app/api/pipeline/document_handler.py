"""Internal API — Handlers for Pub/Sub push subscriptions."""

import base64
import json
import logging
import uuid

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    status,
)
from fastapi import (
    Query as QueryParam,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.db.context import set_user_context
from app.pipeline.orchestrator import process_document
from app.services.push_notification_service import (
    notify_document_processed,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Internal Pipeline"])

async def verify_pipeline_key(
    x_pipeline_key: str | None = Header(
        None, alias="X-Pipeline-Key"
    ),
    key: str | None = QueryParam(
        None, alias="key"
    ),
):
    """Verify pipeline API key (header or query param)."""
    if not settings.pipeline_api_key:
        if settings.environment in ("development", "test"):
            return
        raise HTTPException(
            503, "Pipeline API key not configured"
        )

    provided = x_pipeline_key or key
    if provided != settings.pipeline_api_key:
        raise HTTPException(401, "Invalid pipeline API key")


@router.post(
    "/api/pipeline/document-received",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_pipeline_key)]
)
async def handle_document_received_push(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Handle document.received event pushed by Pub/Sub."""
    # Pub/Sub push payload has the message in 'message.data' (base64)
    try:
        if "message" in payload and "data" in payload["message"]:
            raw_data = payload["message"]["data"]
            decoded_data = base64.b64decode(raw_data).decode("utf-8")
            event_envelope = json.loads(decoded_data)
            event_payload = event_envelope.get("payload", {})
            user_id_str = event_envelope.get("user_id")
        else:
            # Direct call for testing
            event_payload = payload.get("payload", payload)
            user_id_str = payload.get("user_id")

        document_id = uuid.UUID(event_payload["document_id"])
        user_id = uuid.UUID(user_id_str)
    except (KeyError, ValueError, TypeError) as e:
        logger.error("Invalid Pub/Sub payload: %s", payload)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid payload: {e}",
        ) from e

    logger.info(
        "Pub/Sub: Starting pipeline for doc %s",
        document_id,
    )

    # RLS tenant context for the whole pipeline transaction (WS1 Phase 2e). The
    # pipeline writes documents / document_chunks / pending_reviews /
    # questions_tracker / bills / appointments on THIS session up to the commit
    # below; without the GUC those fail-closed once the tables go under RLS. This
    # endpoint authenticates via the pipeline key (not get_current_user), so it
    # must set the context itself. Transaction-local.
    await set_user_context(db, user_id)

    try:
        result = await process_document(
            db, document_id, user_id
        )
        await db.commit()

        # The commit above ended the transaction, which cleared the
        # transaction-local tenant GUC. notify_document_processed reads (and
        # prunes) the member's device_tokens — an RLS tenant table — so re-set
        # the context for this second transaction or the token lookup
        # fail-closes to zero and the push is silently dropped.
        await set_user_context(db, user_id)
        summary = (
            result.summarization.card_summary or ""
        )
        await notify_document_processed(
            db, user_id, summary
        )
        await db.commit()

        return {
            "status": "processed",
            "document_id": str(document_id),
        }
    except Exception as exc:
        await db.rollback()
        logger.exception(
            "Pipeline failed for doc %s", document_id
        )
        raise HTTPException(
            status_code=500, detail="Pipeline failed"
        ) from exc
