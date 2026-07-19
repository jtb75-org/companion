"""Pipeline event publisher.

Firestore-backed pipeline observability events were retired in the self-hosted
migration (document status now lives in the DB — ``documents.status`` + the admin
pipeline-health endpoint). This module remains as a no-op seam so callers need not
change; ``publish_pipeline_event`` returns immediately.
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)

_warned_disabled = False


async def publish_pipeline_event(
    document_id: str,
    stage: str,
    status: str,
    metadata: dict | None = None,
    user_id: str | None = None,
) -> None:
    """No-op: Firestore pipeline events were retired in the migration.

    Document status lives in the DB now. The ``firestore_pipeline_events`` setting is
    vestigial — if it is ever enabled we log once and still do nothing, since the
    Firestore backend (and the firebase-admin dependency) has been removed.
    """
    if not settings.firestore_pipeline_events:
        return
    global _warned_disabled
    if not _warned_disabled:
        _warned_disabled = True
        logger.warning(
            "firestore_pipeline_events is enabled but Firestore pipeline events were "
            "retired in the self-hosted migration — ignoring. Document status lives in "
            "the DB (documents.status)."
        )
