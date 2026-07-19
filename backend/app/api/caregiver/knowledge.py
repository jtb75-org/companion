import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import resolve_caregiver_session
from app.config import settings
from app.db import get_db
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
    """
    # Resolve the caregiver's verified email from the Authentik BFF session.
    email = None
    if settings.dev_auth_bypass and not request.headers.get("Authorization"):
        email = "dev@companion.app"
    else:
        email = await resolve_caregiver_session(request)

    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Enforce token/query quota accounting
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
        logger.exception("Error in regulation knowledge search endpoint")
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while retrieving knowledge answers: {str(e)}",
        ) from e


@router.post("/knowledge/ingest/ecfr", response_model=IngestionStatusResponse)
async def ingest_ecfr(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Trigger ingestion of eCFR Title 20 Part 404 & 416 regulatory HTML documents."""
    # For development/testing or privileged users
    email = None
    if settings.dev_auth_bypass and not request.headers.get("Authorization"):
        email = "dev@companion.app"
    else:
        email = await resolve_caregiver_session(request)

    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        count = await knowledge_service.trigger_ecfr_ingestion(db, parts=[404, 416])
        return IngestionStatusResponse(status="success", chunks_ingested=count)
    except Exception as e:
        logger.exception("Error triggering eCFR ingestion")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to ingest eCFR documents: {str(e)}",
        ) from e


@router.post("/knowledge/ingest/fedreg", response_model=IngestionStatusResponse)
async def ingest_federal_register(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Trigger ingestion of active Federal Register rules regarding the SSA."""
    email = None
    if settings.dev_auth_bypass and not request.headers.get("Authorization"):
        email = "dev@companion.app"
    else:
        email = await resolve_caregiver_session(request)

    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        count = await knowledge_service.trigger_federal_register_ingestion(db)
        return IngestionStatusResponse(status="success", chunks_ingested=count)
    except Exception as e:
        logger.exception("Error triggering Federal Register ingestion")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to ingest Federal Register rules: {str(e)}",
        ) from e

