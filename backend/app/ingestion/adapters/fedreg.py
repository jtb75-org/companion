"""Federal Register adapter — recent SSA disability rules.

Source model: a PERMANENT, append-only dated feed. A doc that is not in "recent
results" this run has NOT been removed — it is simply older, so the purge policy
is RETAIN (absence NEVER deletes). Aging is handled by the reconciler's
retention sweep, which drops docs whose publication date is older than the
rolling window (24 months).

``source_id`` is the Federal Register document number (e.g. "2024-12345") —
stable and globally unique. This adapter loads Federal Register content for the
FIRST time in production (the manual path was never run there).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime

import httpx

from app.ingestion.types import Adapter, IngestionMode, PurgePolicy, SourceDoc

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; DDCompanionRegBot/1.0; +https://mydailydignity.com)"
)

# Rolling retention window: Federal Register docs older than this are aged out by
# the retention sweep (RECONCILE mode) rather than by absence-purge.
_RETENTION_MONTHS = 24


class FederalRegisterAdapter(Adapter):
    source_corpus = "Federal_Register"
    purge_policy = PurgePolicy.RETAIN
    retention_months = _RETENTION_MONTHS
    # RETAIN sources are not fetch-guarded on emptiness (a quiet week is legitimate
    # and purges nothing); leave the floor at 0.
    min_expected_docs = 0

    def __init__(self, *, per_page: int = 20, timeout: float = 30.0) -> None:
        self._per_page = per_page
        self._timeout = timeout

    def _url(self) -> str:
        return (
            "https://www.federalregister.gov/api/v1/documents.json"
            "?conditions[agencies][]=social-security-administration"
            f"&conditions[type][]=RULE&per_page={self._per_page}"
        )

    async def list_documents(self, mode: IngestionMode) -> Iterable[SourceDoc]:
        # Both incremental and reconcile fetch the recent-rules feed and APPEND new
        # document numbers. The retention sweep (reconcile mode) is what ages docs
        # out — handled by the reconciler, not here.
        headers = {"User-Agent": _USER_AGENT}
        logger.info("Querying Federal Register for recent SSA rules...")
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            resp = await client.get(self._url())
        if resp.status_code != 200:
            raise RuntimeError(
                f"Federal Register API returned HTTP {resp.status_code}"
            )

        results = resp.json().get("results", [])
        docs: list[SourceDoc] = []
        for item in results:
            title = item.get("title", "")
            abstract = item.get("abstract", "") or item.get("excerpts", "")
            doc_num = item.get("document_number", "")
            if not title or not abstract or not doc_num:
                continue

            pub_date: datetime | None = None
            pub_date_str = item.get("publication_date", "")
            if pub_date_str:
                try:
                    pub_date = datetime.strptime(pub_date_str, "%Y-%m-%d")
                except ValueError:
                    pub_date = None

            docs.append(
                SourceDoc(
                    source_id=doc_num,
                    text=f"Title: {title}\nSummary: {abstract}",
                    metadata={
                        "jurisdiction": "US_Federal",
                        "source_corpus": self.source_corpus,
                        "source_url": item.get(
                            "html_url", "https://www.federalregister.gov"
                        ),
                        "citation": f"Federal Register Vol. {doc_num}",
                        "program": "Both",
                        "effective_date": pub_date,
                    },
                )
            )
        logger.info("Federal Register adapter yielded %d rule doc(s)", len(docs))
        return docs
