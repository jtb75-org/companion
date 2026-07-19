import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_admin
from app.auth.principal import resolve_caregiver_session
from app.config import settings
from app.db import get_db
from app.models.admin_user import AdminUser
from app.schemas.knowledge import (
    IngestionStatusResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.services import knowledge_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Caregiver Knowledge"])


@router.post("/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: Request,
    payload: KnowledgeSearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Grounded vector RAG search against federal disability policy regulations.

    Queries pgvector, enforces search quotas, and formats standard citations and timeline dates.
    Refuses/redirects on clinical recommendation or state-specific questions.

    CAREGIVER-authed (read-only). Ingestion is admin-only (see below).
    """
    # Resolve the caregiver's verified email from the Authentik BFF session.
    email = None
    if settings.dev_auth_bypass and not request.headers.get("Authorization"):
        email = "dev@companion.app"
    else:
        email = await resolve_caregiver_session(request)

    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Enforce token/query quota accounting. NOTE: check_and_increment_quota FAILS OPEN when
    # Redis is down (permits the search) — see its docstring.
    await knowledge_service.check_and_increment_quota(email, limit=50)

    try:
        # Perform regulation RAG query and LLM processing
        res = await knowledge_service.generate_rag_answer(
            db=db,
            query_text=payload.query,
            program_filter=payload.program,
            limit=payload.limit,
        )
        return res
    except Exception as e:
        # Keep the exception detail in the server log only; return a generic message so an
        # internal error string never reaches the client.
        logger.exception("Error in regulation knowledge search endpoint")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while retrieving knowledge answers.",
        ) from e


# ── Ingestion (ADMIN-ONLY) ──────────────────────────────────────────────────────
# These endpoints fetch external URLs, embed, and DELETE + re-insert the entire corpus.
# They must NOT be reachable by an ordinary caregiver session (which resolve_caregiver_
# session returns for ANY resolvable session with no role check), or any authenticated
# caregiver could wipe/re-ingest the corpus (cost/DoS + integrity). Gate on
# get_current_admin — same admin dependency used by app/api/admin/* — so only an active
# admin_users row can trigger a rebuild. In prod these are also blockable at the edge; the
# long-term home is an internal/CronJob path.


@router.post("/knowledge/ingest/ecfr", response_model=IngestionStatusResponse)
async def ingest_ecfr(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Trigger ingestion of eCFR Title 20 Part 404 & 416 regulatory HTML documents.

    ADMIN-ONLY: full-corpus DELETE + re-insert; non-admins are rejected by
    get_current_admin (401 no session / 403 not an admin)."""
    try:
        count = await knowledge_service.trigger_ecfr_ingestion(db, parts=[404, 416])
        return IngestionStatusResponse(status="success", chunks_ingested=count)
    except Exception as e:
        logger.exception("Error triggering eCFR ingestion")
        raise HTTPException(
            status_code=500,
            detail="Failed to ingest eCFR documents.",
        ) from e


@router.post("/knowledge/ingest/fedreg", response_model=IngestionStatusResponse)
async def ingest_federal_register(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Trigger ingestion of active Federal Register rules regarding the SSA.

    ADMIN-ONLY: full-corpus DELETE + re-insert; non-admins are rejected by
    get_current_admin (401 no session / 403 not an admin)."""
    try:
        count = await knowledge_service.trigger_federal_register_ingestion(db)
        return IngestionStatusResponse(status="success", chunks_ingested=count)
    except Exception as e:
        logger.exception("Error triggering Federal Register ingestion")
        raise HTTPException(
            status_code=500,
            detail="Failed to ingest Federal Register rules.",
        ) from e
