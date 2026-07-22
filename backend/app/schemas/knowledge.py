from pydantic import BaseModel, Field


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., description="The query string to search regulations for")
    program: str | None = Field(
        None, description="Optional program filter: 'SSDI', 'SSI', or 'Both'"
    )
    limit: int = Field(5, ge=1, le=10, description="Max number of sources to retrieve")


class SourceChunkInfo(BaseModel):
    citation: str
    source_corpus: str
    source_url: str
    text_content: str
    program: str
    similarity: float


class KnowledgeSearchResponse(BaseModel):
    query: str
    # ``answer`` always carries the server-appended provenance line and not-legal-advice
    # disclaimer (see knowledge_service.generate_rag_answer) — these are enforced in code,
    # never left to the (untrusted) model. Use it for API/copy-paste (self-contained).
    answer: str
    # ``body`` is the same grounded prose WITHOUT the provenance/disclaimer wrapper, so a
    # UI can render it once and show provenance + disclaimer structurally (below) without
    # duplicating them. The disclaimer must still always render from its own field.
    body: str
    # Server-computed provenance/as-of line and disclaimer, surfaced structurally so a
    # client can render them independently of the model prose.
    provenance: str
    disclaimer: str
    # Citation labels derived server-side from the retrieved chunks, independent of the
    # model text. ``grounded`` is False when no chunk cleared retrieval (no citation).
    citations: list[str]
    grounded: bool
    sources: list[SourceChunkInfo]


class IngestionStatusResponse(BaseModel):
    status: str
    chunks_ingested: int


# ── Public benefits-helper endpoint (Phase 2, UNAUTHENTICATED) ─────────────────
#
# POST /public/knowledge/ask. No auth, no PHI, no member/document_chunks path —
# only the public federal-regulation corpus. The answer contract MIRRORS
# KnowledgeSearchResponse (answer/provenance/disclaimer/citations/grounded) so the
# web widget can share rendering, PLUS anonymous free-question quota fields.


class PublicKnowledgeAskRequest(BaseModel):
    # ``max_length`` bounds the input at the SCHEMA layer (422 before any
    # embedding/LLM work). The endpoint additionally enforces
    # settings.public_knowledge_max_question_chars as the authoritative,
    # configurable cap — an over-long prompt is a cost/abuse vector on an
    # unauthenticated surface. ``min_length=1`` rejects empty questions.
    question: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="The disability-benefits question to answer from federal regulations",
    )
    program: str | None = Field(
        None, description="Optional program filter: 'SSDI', 'SSI', or 'Both'"
    )


class PublicKnowledgeAskResponse(BaseModel):
    # Mirrors KnowledgeSearchResponse. When ``gated`` is True the request was NOT
    # answered (free allowance exhausted or quota store unavailable): ``answer`` is
    # a deterministic sign-up invitation, ``grounded`` is False, ``citations`` and
    # ``sources`` are empty, and NO LLM was called. The disclaimer is always
    # present.
    answer: str
    # Grounded prose WITHOUT the provenance/disclaimer wrapper (see
    # KnowledgeSearchResponse.body) — the UI renders this and shows provenance +
    # disclaimer structurally once, instead of duplicating them.
    body: str
    provenance: str
    disclaimer: str
    citations: list[str]
    grounded: bool
    sources: list[SourceChunkInfo]
    # Anonymous free-question quota state for the "N free, then sign up" hook.
    questions_remaining: int = Field(
        ..., description="Free questions left for this anonymous session after this call"
    )
    gated: bool = Field(
        ..., description="True when the free allowance is exhausted; answer is a sign-up prompt"
    )
