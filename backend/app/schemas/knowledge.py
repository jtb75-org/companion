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
    answer: str
    sources: list[SourceChunkInfo]


class IngestionStatusResponse(BaseModel):
    status: str
    chunks_ingested: int
