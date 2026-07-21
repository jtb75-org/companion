"""eCFR adapter — Title 20 (SSDI Part 404, SSI Part 416).

Source model: a CURRENT snapshot. Full reconcile per part; a section that
vanishes from the source means the regulation was genuinely removed, so the
purge policy is DELETE (guarded by the reconciler's mass-purge breaker).

``source_id`` is the citation (e.g. "20 CFR § 404.1520") — stable across runs.
Reuses the section-aware :class:`ECFRHTMLParser` from ``knowledge_service`` so
parsing behaviour stays identical to the retired manual path.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from app.ingestion.types import Adapter, IngestionMode, PurgePolicy, SourceDoc
from app.services.knowledge_service import ECFRHTMLParser

logger = logging.getLogger(__name__)

# A browser-like UA — the bare httpx/urllib UA is blocked by Cloudflare in front
# of some federal endpoints (see the opencode-litellm note).
_USER_AGENT = (
    "Mozilla/5.0 (compatible; DDCompanionRegBot/1.0; +https://mydailydignity.com)"
)


class ECFRAdapter(Adapter):
    source_corpus = "eCFR"
    purge_policy = PurgePolicy.DELETE

    def __init__(
        self,
        parts: list[int] | None = None,
        *,
        min_expected_docs: int = 50,
        timeout: float = 60.0,
    ) -> None:
        # 404 = SSDI (Title II), 416 = SSI (Title XVI).
        self.parts = parts or [404, 416]
        # A healthy Title 20 pull is hundreds of sections; anything far below this
        # is a broken fetch and must not be allowed to purge (see reconciler).
        self.min_expected_docs = min_expected_docs
        self._timeout = timeout

    def _url(self, part: int) -> str:
        return (
            f"https://www.ecfr.gov/api/renderer/v1/content/enhanced/current"
            f"/title-20?chapter=III&part={part}"
        )

    async def list_documents(self, mode: IngestionMode) -> Iterable[SourceDoc]:
        docs: list[SourceDoc] = []
        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            for part in self.parts:
                url = self._url(part)
                logger.info("Fetching eCFR Title 20 Part %d...", part)
                resp = await client.get(url)
                if resp.status_code != 200:
                    # Raise so the reconciler treats a failed fetch as systemic and
                    # aborts WITHOUT purging (rather than silently yielding fewer docs).
                    raise RuntimeError(
                        f"eCFR fetch for part {part} returned HTTP {resp.status_code}"
                    )

                parser = ECFRHTMLParser(part_num=str(part))
                parser.feed(resp.text)
                if not parser.chunks:
                    logger.warning("No sections parsed for eCFR Part %d", part)
                    continue

                program = "SSDI" if part == 404 else "SSI"
                for chunk in parser.chunks:
                    citation_section = (
                        chunk["section"].replace("p-", "").replace("part-", "").strip()
                    )
                    citation = f"20 CFR § {citation_section}"
                    sect_parts = citation_section.split(".")
                    section_num = (
                        sect_parts[-1] if len(sect_parts) > 1 else citation_section
                    )
                    docs.append(
                        SourceDoc(
                            source_id=citation,
                            text=chunk["text"],
                            metadata={
                                "jurisdiction": "US_Federal",
                                "source_corpus": self.source_corpus,
                                "source_url": url,
                                "citation": citation,
                                "title": "20",
                                "part": str(part),
                                "section": section_num,
                                "program": program,
                                "effective_date": None,
                            },
                        )
                    )
        logger.info("eCFR adapter yielded %d section doc(s)", len(docs))
        return docs
