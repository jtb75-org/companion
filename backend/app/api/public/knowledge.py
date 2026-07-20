"""PUBLIC benefits-helper endpoint (Phase 2).

POST /public/knowledge/ask — an UNAUTHENTICATED grounded-RAG helper over the
PUBLIC federal-regulation corpus (``disability_reg_chunks`` only). It powers the
marketing landing page's "ask a disability-benefits question" widget and the
"N free questions, then sign up" conversion hook.

Security posture (built IN, not later):
  * NO auth / NO session / NO PHI. The only datastore this path touches is the
    public, non-PHI regulation corpus via knowledge_service.generate_rag_answer,
    which queries ``disability_reg_chunks`` exclusively — it never opens the
    per-member PHI RAG (``document_chunks``) path and the two are never
    co-queried. Nothing about a member/user is read or written here.
  * Anonymous free-question quota. A random opaque anonymous-session id (cookie,
    NOT tied to any user/PHI) meters a small number of free LLM answers per
    session in Redis, then GATES with a deterministic sign-up invitation (no LLM
    call). See knowledge_service.check_and_increment_anon_quota.
  * FAIL-CLOSED. If Redis is unavailable the quota check gates rather than hands
    out unmetered public LLM calls (cost/abuse). Opposite of the authed path.
  * Input cap. Over-long questions are rejected (422) BEFORE any embedding/LLM
    work.
  * The not-legal-advice disclaimer, provenance line, structural citations, and
    the grounded=false refusal are all enforced in code inside generate_rag_answer
    (reused unchanged) — never delegated to the untrusted model.

DEFENSE-IN-DEPTH NOTE: this app-layer quota is the AUTHORITATIVE control, but a
public LLM endpoint also needs Cloudflare edge rate-limiting / bot-protection in
front of it (a separate infra task) before real public launch. The edge catch-all
ingress routes ``/`` (only ``/api/internal`` is blocked), so ``/public/*`` is
internet-reachable exactly like ``/health`` and ``/auth/*``.
"""

import logging
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.schemas.knowledge import (
    PublicKnowledgeAskRequest,
    PublicKnowledgeAskResponse,
)
from app.services import knowledge_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Public Knowledge"])

# A well-formed anonymous-session id is an opaque, URL-safe token we minted
# (secrets.token_urlsafe(32) → 43 chars). Reject anything that does not match
# rather than trust an attacker-supplied cookie value as a Redis key fragment; a
# malformed/absent cookie just starts a fresh anonymous session.
_ANON_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# Deterministic gate message shown when the free allowance is exhausted (or the
# quota store is unavailable). Carries NO answer body — the whole point is to stop
# calling the LLM and invite account creation.
_GATE_MESSAGE = (
    "You've used your free disability-benefits questions. Create a free account to "
    "keep asking and to save your answers."
)


def _resolve_anon_id(request: Request) -> str:
    """Return the caller's anonymous-session id, minting a new opaque one if the
    cookie is absent or malformed."""
    raw = request.cookies.get(settings.public_knowledge_anon_cookie_name)
    if raw and _ANON_ID_RE.match(raw):
        return raw
    return secrets.token_urlsafe(32)


def _set_anon_cookie(response: Response, anon_id: str) -> None:
    """Persist the anonymous-session id as an httpOnly, SameSite=Lax cookie.

    ``secure`` follows settings.session_cookie_secure (True in prod). The id is a
    random opaque token; it is NOT a credential and carries no user/PHI data — it
    exists only to count free questions.
    """
    kwargs: dict = {
        "key": settings.public_knowledge_anon_cookie_name,
        "value": anon_id,
        "max_age": settings.public_knowledge_quota_ttl_seconds,
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    if settings.session_cookie_domain:
        kwargs["domain"] = settings.session_cookie_domain
    response.set_cookie(**kwargs)


@router.post("/knowledge/ask", response_model=PublicKnowledgeAskResponse)
async def public_ask(
    request: Request,
    payload: PublicKnowledgeAskRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PublicKnowledgeAskResponse:
    """Answer a disability-benefits question from federal regulations, UNAUTHENTICATED.

    Meters a small number of free answers per anonymous session, then gates with a
    sign-up invitation. No auth, no PHI: touches only the public
    ``disability_reg_chunks`` corpus via generate_rag_answer (reused unchanged).
    """
    # 1. Cost/abuse guard: reject over-long input BEFORE embedding/LLM. The schema
    #    caps at a hard ceiling; this enforces the configurable, authoritative cap.
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question must not be empty.")
    if len(question) > settings.public_knowledge_max_question_chars:
        raise HTTPException(
            status_code=422,
            detail=(
                "Question is too long. Please shorten it to "
                f"{settings.public_knowledge_max_question_chars} characters or fewer."
            ),
        )

    # 2. Resolve/mint the anonymous-session id and (re)persist the cookie so a
    #    returning browser continues the SAME count.
    anon_id = _resolve_anon_id(request)
    _set_anon_cookie(response, anon_id)

    # 3. Anonymous free-question quota. FAIL-CLOSED: a Redis outage gates here
    #    rather than permitting an unmetered public LLM call.
    gated, remaining = await knowledge_service.check_and_increment_anon_quota(
        anon_id,
        limit=settings.public_knowledge_free_limit,
        ttl_seconds=settings.public_knowledge_quota_ttl_seconds,
    )
    if gated:
        # Deterministic gate — NO LLM call, no answer body. Disclaimer still
        # present for consistency with the answer contract.
        return PublicKnowledgeAskResponse(
            answer=_GATE_MESSAGE,
            provenance="",
            disclaimer=knowledge_service.NOT_LEGAL_ADVICE_DISCLAIMER,
            citations=[],
            grounded=False,
            sources=[],
            questions_remaining=0,
            gated=True,
        )

    # 4. Grounded RAG answer over the PUBLIC regulation corpus ONLY. generate_rag_
    #    answer queries disability_reg_chunks exclusively (no PHI/document_chunks
    #    path) and enforces disclaimer + provenance + structural citations +
    #    grounded=false refusal in code. ``limit`` is fixed server-side (not client
    #    controlled) to bound retrieval cost on this anonymous surface.
    try:
        res = await knowledge_service.generate_rag_answer(
            db=db,
            query_text=question,
            program_filter=payload.program,
            limit=5,
        )
    except Exception as e:
        # Keep the detail in the server log; return a generic message so no internal
        # error string ever reaches an anonymous caller.
        logger.exception("Error in public knowledge ask endpoint")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while retrieving your answer.",
        ) from e

    return PublicKnowledgeAskResponse(
        answer=res["answer"],
        provenance=res["provenance"],
        disclaimer=res["disclaimer"],
        citations=res["citations"],
        grounded=res["grounded"],
        sources=res["sources"],
        questions_remaining=remaining,
        gated=False,
    )
