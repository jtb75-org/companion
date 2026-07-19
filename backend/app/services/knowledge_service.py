import logging
import re
import uuid
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversation.llm import get_llm_client
from app.db.redis import get_redis
from app.models.regulation_chunk import RegulationChunk
from app.pipeline.embedding_client import embed_documents, embed_query

logger = logging.getLogger(__name__)


# ── eCFR HTML Parser ──────────────────────────────────────────────────────────

class ECFRHTMLParser(HTMLParser):
    """Robust HTMLParser for section-aware hierarchical parsing of eCFR HTML content.

    Specifically captures text from <div class="section" id="XXX"> elements.
    """

    def __init__(self, part_num: str):
        super().__init__()
        self.part_num = part_num
        self.chunks = []
        self.current_section = None
        self.div_stack = []
        self.capture_buffer = []
        self.section_depth = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")
        elem_id = attrs_dict.get("id", "")

        self.div_stack.append({
            "tag": tag,
            "id": elem_id,
            "class": class_name
        })

        if tag == "div" and "section" in class_name:
            self.current_section = elem_id
            self.section_depth = len(self.div_stack)
            self.capture_buffer = []

        if self.current_section is not None:
            # Format text nodes inside elements cleanly
            if tag in ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"]:
                self.capture_buffer.append("\n")

    def handle_endtag(self, tag: str):
        if not self.div_stack:
            return

        self.div_stack.pop()

        # If we are exiting the section div boundary
        if self.current_section is not None and len(self.div_stack) < self.section_depth:
            text_content = "".join(self.capture_buffer).strip()
            # Normalize whitespace
            text_content = re.sub(r"[ \t]+", " ", text_content)
            text_content = re.sub(r"\n\s*", "\n", text_content)
            text_content = re.sub(r"\n+", "\n", text_content).strip()

            if text_content:
                self.chunks.append({
                    "section": self.current_section,
                    "text": text_content
                })

            self.current_section = None
            self.section_depth = None
            self.capture_buffer = []

    def handle_data(self, data: str):
        if self.current_section is not None:
            self.capture_buffer.append(data)


# ── Quota Verification ────────────────────────────────────────────────────────

async def check_and_increment_quota(email: str, limit: int = 50) -> int:
    """Track and increment per-session query quota in Redis (24-hour window).

    Raises 429 if the quota limit has been exceeded.
    """
    key = f"quota:knowledge:{email}"
    try:
        r = get_redis()
        current = await r.get(key)
        if current is not None:
            count = int(current)
            if count >= limit:
                logger.warning("Quota exceeded for caregiver %s: %d >= %d", email, count, limit)
                raise HTTPException(
                    status_code=429,
                    detail="Knowledge search query limit reached. Please try again tomorrow.",
                )
            new_count = await r.incr(key)
        else:
            # New key, set initial and 24-hour expiry
            new_count = 1
            await r.set(key, 1, ex=24 * 3600)
        await r.aclose()
        return new_count
    except HTTPException:
        raise
    except Exception:
        # Resilient fallback: log error but do not block search if Redis is down
        logger.exception("Failed to verify search quota in Redis. Permitting search as fallback.")
        return 1


# ── Database Capability Checks ────────────────────────────────────────────────

_has_vector_extension = None


async def has_pgvector(db: AsyncSession) -> bool:
    """Check if the pgvector extension is available in the connected database."""
    global _has_vector_extension
    if _has_vector_extension is None:
        try:
            res = await db.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            )
            _has_vector_extension = res.scalar() is not None
        except Exception:
            _has_vector_extension = False
    return _has_vector_extension


# ── Ingestion Service ─────────────────────────────────────────────────────────

async def trigger_ecfr_ingestion(db: AsyncSession, parts: list[int] | None = None) -> int:
    """Pull current eCFR structured HTML, parse into section-level chunks, generate embeddings,

    and insert into disability_reg_chunks table.
    """
    if parts is None:
        parts = [404, 416]

    total_inserted = 0
    has_vector = await has_pgvector(db)

    async with httpx.AsyncClient(timeout=60.0) as client:
        for part in parts:
            # Use the verified HTML renderer URL
            url = (
                f"https://www.ecfr.gov/api/renderer/v1/content/enhanced/current"
                f"/title-20?chapter=III&part={part}"
            )
            logger.info("Fetching eCFR Title 20 Part %d content...", part)

            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.error(
                        "Failed to fetch eCFR for part %d. Status: %d",
                        part,
                        resp.status_code,
                    )
                    continue

                html_content = resp.text
                parser = ECFRHTMLParser(part_num=str(part))
                parser.feed(html_content)

                parsed_chunks = parser.chunks
                if not parsed_chunks:
                    logger.warning("No parsed chunks found for eCFR Part %d", part)
                    continue

                logger.info("Found %d raw section chunks in eCFR Part %d", len(parsed_chunks), part)

                # Evict existing eCFR chunks for this part to prevent duplicates
                await db.execute(
                    delete(RegulationChunk).where(
                        RegulationChunk.source_corpus == "eCFR",
                        RegulationChunk.part == str(part)
                    )
                )

                # Process in batches of 50 for embedding generation
                batch_size = 50
                for i in range(0, len(parsed_chunks), batch_size):
                    batch = parsed_chunks[i:i + batch_size]
                    texts = [chunk["text"] for chunk in batch]

                    # Generate embeddings via shared gateway
                    embeddings = await embed_documents(texts)

                    for chunk_data, embedding in zip(batch, embeddings, strict=True):
                        text_content = chunk_data["text"]
                        citation_section = (
                            chunk_data["section"]
                            .replace("p-", "")
                            .replace("part-", "")
                            .strip()
                        )

                        # Determine program context from section part
                        program = "SSDI" if part == 404 else "SSI"

                        # Build citation label
                        citation_label = f"20 CFR § {citation_section}"

                        sect_parts = citation_section.split(".")
                        sect_num = sect_parts[-1] if len(sect_parts) > 1 else citation_section

                        # Dynamically insert to survive absence of pgvector in testing
                        columns = [
                            "id", "jurisdiction", "source_corpus", "source_url",
                            "citation", "title", "part", "section", "program",
                            "text_content", "token_count", "effective_date"
                        ]
                        placeholders = [f":{col}" for col in columns]
                        params = {
                            "id": str(uuid.uuid4()),
                            "jurisdiction": "US_Federal",
                            "source_corpus": "eCFR",
                            "source_url": url,
                            "citation": citation_label,
                            "title": "20",
                            "part": str(part),
                            "section": sect_num,
                            "program": program,
                            "text_content": text_content,
                            "token_count": len(text_content) // 4,
                            "effective_date": datetime.now()
                        }

                        if has_vector:
                            columns.append("embedding")
                            placeholders.append(":embedding")
                            params["embedding"] = str(embedding)

                        sql = (
                            f"INSERT INTO disability_reg_chunks ({', '.join(columns)}) "
                            f"VALUES ({', '.join(placeholders)})"
                        )
                        await db.execute(text(sql), params)

                    await db.flush()
                    total_inserted += len(batch)

                logger.info(
                    "Successfully ingested %d chunks for eCFR Part %d",
                    total_inserted,
                    part,
                )

            except Exception:
                logger.exception("Error ingesting eCFR Part %d", part)
                raise

    await db.commit()
    return total_inserted


async def trigger_federal_register_ingestion(db: AsyncSession) -> int:
    """Query recent Federal Register rules for SSA and ingest them into the knowledge store."""
    total_inserted = 0
    has_vector = await has_pgvector(db)

    url = (
        "https://www.federalregister.gov/api/v1/documents.json"
        "?conditions[agencies][]=social-security-administration"
        "&conditions[type][]=RULE&per_page=20"
    )

    logger.info("Querying Federal Register for recent rules...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.error("Failed to query Federal Register API. Status: %d", resp.status_code)
                return 0

            data = resp.json()
            results = data.get("results", [])
            if not results:
                logger.warning("No recent Federal Register rules found")
                return 0

            # Evict existing Federal Register rules
            await db.execute(
                delete(RegulationChunk).where(RegulationChunk.source_corpus == "Federal_Register")
            )

            texts_to_embed = []
            valid_rules = []

            for item in results:
                title = item.get("title", "")
                abstract = item.get("abstract", "") or item.get("excerpts", "")
                doc_num = item.get("document_number", "")
                pub_date_str = item.get("publication_date", "")

                if not title or not abstract or not doc_num:
                    continue

                combined_text = f"Title: {title}\nSummary: {abstract}"
                texts_to_embed.append(combined_text)

                pub_date = None
                if pub_date_str:
                    try:
                        pub_date = datetime.strptime(pub_date_str, "%Y-%m-%d")
                    except ValueError:
                        pass

                valid_rules.append({
                    "text": combined_text,
                    "citation": f"Federal Register Vol. {doc_num}",
                    "source_url": item.get("html_url", "https://www.federalregister.gov"),
                    "effective_date": pub_date or datetime.now()
                })

            if texts_to_embed:
                embeddings = await embed_documents(texts_to_embed)

                for rule, embedding in zip(valid_rules, embeddings, strict=True):
                    columns = [
                        "id", "jurisdiction", "source_corpus", "source_url",
                        "citation", "program", "text_content", "token_count",
                        "effective_date"
                    ]
                    placeholders = [f":{col}" for col in columns]
                    params = {
                        "id": str(uuid.uuid4()),
                        "jurisdiction": "US_Federal",
                        "source_corpus": "Federal_Register",
                        "source_url": rule["source_url"],
                        "citation": rule["citation"],
                        "program": "Both",
                        "text_content": rule["text"],
                        "token_count": len(rule["text"]) // 4,
                        "effective_date": rule["effective_date"]
                    }

                    if has_vector:
                        columns.append("embedding")
                        placeholders.append(":embedding")
                        params["embedding"] = str(embedding)

                    sql = (
                        f"INSERT INTO disability_reg_chunks ({', '.join(columns)}) "
                        f"VALUES ({', '.join(placeholders)})"
                    )
                    await db.execute(text(sql), params)

                await db.flush()
                total_inserted = len(valid_rules)

            logger.info("Successfully ingested %d Federal Register rule chunks", total_inserted)

        except Exception:
            logger.exception("Error ingesting Federal Register rules")
            raise

    await db.commit()
    return total_inserted


# ── Search & Answer Generation ────────────────────────────────────────────────

async def search_regulations(
    db: AsyncSession, query_text: str, program_filter: str | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    """Query the regulation chunks index using pgvector cosine similarity.

    Falls back to a keyword ILIKE text search if pgvector is not available in the DB context.
    """
    has_vector = await has_pgvector(db)

    if not has_vector:
        # Fallback to ILIKE text search if pgvector is not available (e.g. in test environment)
        logger.warning("pgvector is not available. Falling back to keyword search.")
        sql_text = """
            SELECT
                id,
                jurisdiction,
                source_corpus,
                source_url,
                citation,
                program,
                text_content,
                effective_date,
                0.85 AS similarity
            FROM disability_reg_chunks
            WHERE (CAST(:program_filter AS VARCHAR) IS NULL 
                   OR program = CAST(:program_filter AS VARCHAR) 
                   OR program = 'Both')
              AND (text_content ILIKE :query_like OR citation ILIKE :query_like)
            LIMIT :limit
        """
        # Extract words, filter out stopwords, and choose the longest word as the key term
        words = [re.sub(r"\W+", "", w) for w in re.split(r"\s+", query_text)]
        stopwords = {
            "what", "how", "the", "are", "and", "why", "who", "where", "when",
            "this", "that", "with", "from", "your", "they", "them", "have", "been"
        }
        words = [w for w in words if len(w) > 2 and w.lower() not in stopwords]
        words.sort(key=len, reverse=True)
        like_pattern = f"%{words[0]}%" if words else "%"

        res = await db.execute(
            text(sql_text),
            {
                "program_filter": program_filter,
                "query_like": like_pattern,
                "limit": limit
            }
        )
    else:
        query_embedding = await embed_query(query_text)
        query_vec_str = str(query_embedding)

        sql_text = """
            SELECT
                id,
                jurisdiction,
                source_corpus,
                source_url,
                citation,
                program,
                text_content,
                effective_date,
                1 - (embedding <=> :query_vec) AS similarity
            FROM disability_reg_chunks
            WHERE (CAST(:program_filter AS VARCHAR) IS NULL 
                   OR program = CAST(:program_filter AS VARCHAR) 
                   OR program = 'Both')
            ORDER BY embedding <=> :query_vec
            LIMIT :limit
        """
        res = await db.execute(
            text(sql_text),
            {
                "query_vec": query_vec_str,
                "program_filter": program_filter,
                "limit": limit
            }
        )

    rows = res.fetchall()
    results = []

    for r in rows:
        sim = float(r.similarity)
        # Apply a conservative relevance threshold
        if sim < 0.3:
            continue

        results.append({
            "id": r.id,
            "jurisdiction": r.jurisdiction,
            "source_corpus": r.source_corpus,
            "source_url": r.source_url,
            "citation": r.citation,
            "program": r.program,
            "text_content": r.text_content,
            "effective_date": r.effective_date,
            "similarity": sim
        })

    return results


async def generate_rag_answer(
    db: AsyncSession, query_text: str, program_filter: str | None = None, limit: int = 5
) -> dict[str, Any]:
    """Perform regulation vector retrieval and pass standard chunks as grounding to the LLM.

    Strictly honors refusals, redirects, formatting disclaimers, and dates.
    """
    # 1. Retrieve grounded chunks
    chunks = await search_regulations(db, query_text, program_filter, limit)

    # 2. Extract standard variables for timeline dates (provenance)
    effective_dates = [c["effective_date"] for c in chunks if c["effective_date"] is not None]
    newest_date = max(effective_dates) if effective_dates else datetime.now()
    provenance_str = f"As of {newest_date.strftime('%B %d, %Y')}"

    # 3. Format chunks as structural context
    context_blocks = []
    for i, c in enumerate(chunks):
        block = (
            f"Source [{i + 1}]: {c['citation']} ({c['source_corpus']})\n"
            f"Url: {c['source_url']}\n"
            f"Content:\n{c['text_content']}"
        )
        context_blocks.append(block)
    chunks_context = (
        "\n\n---\n\n".join(context_blocks)
        if context_blocks
        else "No relevant regulation chunks found."
    )

    # 4. Prompt construction with short, Ruff-compliant lines
    system_prompt = (
        "You are a highly precise, authoritative Caregiver Knowledge Assistant.\n"
        "Your primary role is to answer questions about U.S. Federal Disability Policy "
        "regulations (specifically SSDI and SSI under 20 CFR) using the provided "
        "official regulation chunks.\n\n"
        "Your behavioral guidelines:\n"
        "1. Ground every statement: Only answer using the exact, provided regulation chunks. "
        "Do NOT make up, assume, or extrapolate anything beyond these texts. If the retrieved "
        "context does not contain enough information to answer, state clearly \"I cannot "
        "find the answer in the official retrieved regulation chunks.\"\n"
        "2. Citation requirements: For every claim, rule, or requirement you state, you MUST "
        "cite the specific part, section, or source (e.g., \"20 CFR § 404.1520\") from the "
        "chunks. Format citations inline or at the end of sentences.\n"
        "3. Provenance requirement: Your response must begin with a timeline line containing: "
        f"\"Provenance: {provenance_str}.\"\n"
        "4. Disclaimer requirement: Your response must end with this exact disclaimer on its "
        "own line:\n"
        "\"Disclaimer: I am an AI assistant helping you look up federal regulations. "
        "This is not legal, financial, or professional advice. Always verify with the "
        "Social Security Administration or a qualified professional.\"\n"
        "5. Refuse and redirect: If the user asks about:\n"
        "   - State-specific regulations or state-run eligibility rules (such as state Medicaid "
        "or specific county support programs).\n"
        "   - Personal eligibility determinations (e.g. \"Do I qualify?\", \"Will I get SSI?\").\n"
        "   - Legal, medical, or clinical recommendations (e.g. \"Should I appeal?\", "
        "\"What doctor should I see?\").\n"
        "   Then politely refuse to answer, explaining that you cannot answer state-specific, "
        "eligibility, or professional recommendation questions, and redirect them to contact "
        "the Social Security Administration, their local caseworker, or their legal advocate.\n\n"
        f"Provided regulation chunks:\n{chunks_context}\n"
    )

    messages = [
        {"role": "user", "content": query_text}
    ]

    # 5. Call LLM Client
    llm = get_llm_client()
    answer_text = await llm.generate(
        system_prompt=system_prompt,
        messages=messages,
        max_tokens=800
    )

    return {
        "query": query_text,
        "answer": answer_text,
        "sources": chunks
    }
