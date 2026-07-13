"""Pipeline event publisher — writes stage updates to Firestore."""

import asyncio
import logging
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)

_firestore_available: bool | None = None


def _get_firestore():
    """Get Firestore client, initializing Firebase if needed."""
    global _firestore_available
    if _firestore_available is False:
        return None

    try:
        from app.auth.firebase import _ensure_initialized

        _ensure_initialized()
        from firebase_admin import firestore

        client = firestore.client()
        _firestore_available = True
        return client
    except Exception:
        logger.warning("Firestore client unavailable")
        _firestore_available = False
        return None


async def publish_pipeline_event(
    document_id: str,
    stage: str,
    status: str,
    metadata: dict | None = None,
    user_id: str | None = None,
) -> None:
    """Write pipeline stage status to Firestore.

    Fails fast (2s timeout) to avoid blocking the pipeline.
    """
    # Firestore was retired in the self-hosted migration; skip entirely when
    # disabled (default) so the pipeline doesn't attempt a doomed write and log
    # a warning per stage. Document status lives in the DB now.
    if not settings.firestore_pipeline_events:
        return

    doc_id = str(document_id)

    try:
        db = _get_firestore()
        if db is None:
            return

        update = {
            stage: status,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if user_id:
            update["user_id"] = str(user_id)
        if metadata:
            update[f"{stage}_metadata"] = metadata

        await asyncio.wait_for(
            asyncio.to_thread(
                db.collection("pipeline_events")
                .document(doc_id)
                .set,
                update,
                merge=True,
            ),
            timeout=2.0,
        )
    except TimeoutError:
        logger.warning(
            "Firestore write timed out for doc %s",
            document_id,
        )
    except Exception:
        logger.warning(
            "Failed to write pipeline event to Firestore"
            " for doc %s",
            document_id,
        )
