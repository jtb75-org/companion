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
    # never left to the (untrusted) model.
    answer: str
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
