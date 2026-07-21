"""eCFR adapter — Title 20 (SSDI Part 404, SSI Part 416) + the Blue Book listings.

Source model: a CURRENT snapshot. Full reconcile per part; a section that
vanishes from the source means the regulation was genuinely removed, so the
purge policy is DELETE (guarded by the reconciler's mass-purge breaker).

Two shapes are pulled from the same source:

  1. Section bodies of Part 404/416 (the enhanced renderer, one div per section)
     parsed by :class:`ECFRHTMLParser` — ``source_id`` = the citation
     (e.g. "20 CFR § 404.1520").
  2. Appendix 1 to Subpart P of Part 404 — the **Listing of Impairments**
     ("Blue Book"): the medical body-system listings that drive step-3
     eligibility (e.g. 12.04 Depressive disorders, 1.15 skeletal spine). The
     enhanced renderer returns the whole appendix as ONE ``<div class="appendix">``
     with a flat paragraph stream, so :class:`_AppendixBlockParser` +
     :func:`_parse_listing_appendix` segment it into one :class:`SourceDoc` per
     individual listing (and one per body-system narrative). Listing docs use a
     distinct citation form — "20 CFR Pt. 404, Subpt. P, App. 1, § 12.04" — that
     never collides with the section ``source_id``s above.

The listings are ingested ONCE under Part 404. Part 416 (SSI) has no listings
appendix of its own — 20 CFR 416.925/416.926 incorporate 404 Subpart P
Appendix 1 by reference — so they are marked ``program="Both"`` rather than
duplicated. Appendix 2 (Medical-Vocational Guidelines / the step-5 "grids") is
table-structured and needs a different parser; it is intentionally deferred (see
``include_appendix``/module TODO), not ingested here.

Reuses the section-aware :class:`ECFRHTMLParser` from ``knowledge_service`` for
the section bodies so that behaviour stays identical to the retired manual path.
Oversized listing/narrative docs are NOT chunked here — the reconciler
sub-chunks every doc via ``knowledge_service._split_text_for_embedding``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from html.parser import HTMLParser

import httpx

from app.ingestion.types import Adapter, IngestionMode, PurgePolicy, SourceDoc
from app.services.knowledge_service import ECFRHTMLParser

logger = logging.getLogger(__name__)

# A browser-like UA — the bare httpx/urllib UA is blocked by Cloudflare in front
# of some federal endpoints (see the opencode-litellm note).
_USER_AGENT = (
    "Mozilla/5.0 (compatible; DDCompanionRegBot/1.0; +https://mydailydignity.com)"
)

# A listing/body-system header line begins with a dotted number: "12.04 ...",
# "12.00 ...", "5.03-5.04 [Reserved]". Group 1 = body-system number (1-3 digits;
# childhood listings are 1NN), group 2 = the two-digit listing number, group 3 =
# the remaining title/body on that line.
_LISTING_HEAD_RE = re.compile(
    r"^(\d{1,3})\.(\d{2})(?:[-–]\d{1,3}\.\d{2})?\.?\s+(.*)", re.DOTALL
)
_PART_RE = re.compile(r"^Part\s+([AB])\b")

# Editorial/banner containers the eCFR renderer nests INSIDE the appendix div
# (provenance boxes, seals, editorial + effective-date notes, cross-references).
# Their text is not regulation content, so blocks inside them are suppressed.
_SKIP_CONTAINER_CLASSES = (
    "seal-block",
    "box",
    "content-block",
    "editorial-note",
    "effective-date-note",
    "fr-cross-reference",
)

# Minimum character length for a body-system narrative to be emitted as its own
# doc — filters the top-of-appendix table-of-contents rows (a bare "1.00
# Musculoskeletal Disorders" with no following body) from the real N.00 sections.
_MIN_NARRATIVE_CHARS = 200

_APPENDIX_LABEL = "Appendix 1 to Subpart P of Part 404"


class _AppendixBlockParser(HTMLParser):
    """Flatten ``<div class="appendix">`` into an ordered list of text blocks.

    The appendix has no per-listing ``<div>``; it is a flat stream of ``<p>``
    (plus headers, list items, table rows). Each such flow element becomes one
    block string, in reading order. Blocks inside editorial/banner containers
    (see ``_SKIP_CONTAINER_CLASSES``) are dropped. Segmentation into listings is
    done separately by :func:`_parse_listing_appendix`.
    """

    _BLOCK_TAGS = frozenset(
        {"p", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "li"}
    )

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self._buf: list[str] = []
        self._depth = 0
        self._app_depth: int | None = None
        self._skip_depth: int | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        classes = (dict(attrs).get("class") or "").split()
        self._depth += 1
        if self._app_depth is None and tag == "div" and "appendix" in classes:
            self._app_depth = self._depth
        if self._app_depth is None:
            return
        # Enter a skip container (only the OUTERMOST one is tracked).
        if self._skip_depth is None and any(
            c in _SKIP_CONTAINER_CLASSES for c in classes
        ):
            self._skip_depth = self._depth
            self._flush()  # a container break also ends the current block
            return
        if self._skip_depth is None and tag in self._BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if self._app_depth is not None and self._depth <= self._app_depth:
            # Leaving the appendix div entirely.
            self._flush()
            self._app_depth = None
        if self._skip_depth is not None and self._depth <= self._skip_depth:
            self._skip_depth = None
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._app_depth is not None and self._skip_depth is None:
            self._buf.append(data)

    def _flush(self) -> None:
        block = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if block:
            self.blocks.append(block)
        self._buf = []

    def close(self) -> None:  # type: ignore[override]
        super().close()
        self._flush()


def _clean_title(text: str) -> str:
    """Trim a header's trailing punctuation into a clean title string."""
    return text.strip().rstrip(".").strip()


def _parse_listing_appendix(
    html: str, *, source_url: str, retrieval_date: datetime | None = None
) -> list[SourceDoc]:
    """Segment the Blue Book appendix HTML into per-listing + per-narrative docs.

    Yields one :class:`SourceDoc` for every individual impairment listing (the
    ``N.02``+ entries, ``[Reserved]`` ones skipped) and one for every body-system
    narrative (the ``N.00`` intro/guidance section). The listing hierarchy —
    Part A/B, body system, listing number/title — is preserved both in each
    doc's ``text`` header and in its ``metadata``.

    Pure/hermetic: operates on the passed HTML with no network. ``source_url`` and
    ``retrieval_date`` are recorded on every doc for provenance.
    """
    parser = _AppendixBlockParser()
    parser.feed(html)
    parser.close()
    blocks = parser.blocks

    docs: list[SourceDoc] = []
    part_letter = "A"
    cur_sys: str | None = None
    cur_sys_title = ""
    # An open listing or narrative accumulator: {"kind","num","title","lines"}.
    open_doc: dict | None = None

    def _flush() -> None:
        nonlocal open_doc
        if open_doc is None:
            return
        body = "\n".join(open_doc["lines"]).strip()
        if open_doc["kind"] == "narrative" and len(body) < _MIN_NARRATIVE_CHARS:
            open_doc = None  # table-of-contents row / empty section
            return
        docs.append(
            _build_appendix_doc(
                kind=open_doc["kind"],
                part_letter=open_doc["part"],
                sys_num=open_doc["sys"],
                sys_title=open_doc["sys_title"],
                num=open_doc["num"],
                title=open_doc["title"],
                body=body,
                source_url=source_url,
                retrieval_date=retrieval_date,
            )
        )
        open_doc = None

    for block in blocks:
        part_m = _PART_RE.match(block)
        if part_m and len(block) <= len("Part A") + 2:
            _flush()
            part_letter = part_m.group(1)
            cur_sys = None
            continue

        head_m = _LISTING_HEAD_RE.match(block)
        if head_m is None:
            if open_doc is not None:
                open_doc["lines"].append(block)
            continue

        sys, sub, rest = head_m.group(1), head_m.group(2), head_m.group(3)
        if sub == "00":
            # Body-system narrative section header (e.g. "12.00 Mental Disorders").
            _flush()
            cur_sys = sys
            cur_sys_title = _clean_title(rest)
            open_doc = {
                "kind": "narrative",
                "part": part_letter,
                "sys": sys,
                "sys_title": cur_sys_title,
                "num": f"{sys}.00",
                "title": cur_sys_title,
                "lines": [f"{sys}.00 {rest}".strip()],
            }
            continue
        if sub == "01":
            # "N.01 Category of Impairments" — a pure divider before the listings.
            _flush()
            cur_sys = sys
            continue

        # A same-body-system listing header (its prefix matches the section we are
        # inside — this guards against formula/reference numbers like "9.57 ×" that
        # appear mid-criteria and belong to the open listing's body).
        if cur_sys is not None and sys == cur_sys:
            # Close the previous listing regardless: a new listing header ends it,
            # and a [Reserved] slot marks the boundary of the previous listing too.
            _flush()
            if "[Reserved]" in block:
                # Reserved slots carry no criteria — do NOT open a doc for them and,
                # critically, do NOT let the header fall through and get appended to
                # the previously-open listing (that would bolt "12.03 [Reserved]"
                # onto 12.02's text). Skip the block entirely.
                continue
            open_doc = {
                "kind": "listing",
                "part": part_letter,
                "sys": sys,
                "sys_title": cur_sys_title,
                "num": f"{sys}.{sub}",
                "title": _clean_title(rest),
                "lines": [block],
            }
        elif open_doc is not None:
            open_doc["lines"].append(block)

    _flush()
    logger.info(
        "eCFR appendix parser yielded %d listing/narrative doc(s)", len(docs)
    )
    return docs


def _build_appendix_doc(
    *,
    kind: str,
    part_letter: str,
    sys_num: str,
    sys_title: str,
    num: str,
    title: str,
    body: str,
    source_url: str,
    retrieval_date: datetime | None,
) -> SourceDoc:
    part_label = "Part A (Adults)" if part_letter == "A" else "Part B (Children)"
    citation = f"20 CFR Pt. 404, Subpt. P, App. 1, § {num}"
    if kind == "listing":
        header = (
            f"20 CFR Part 404, Subpart P, Appendix 1 — Listing of "
            f"Impairments ({part_label})\n"
            f"Body system {sys_num}.00 {sys_title}\n"
            f"Listing {num} — {title}\n"
        )
    else:
        header = (
            f"20 CFR Part 404, Subpart P, Appendix 1 — Listing of "
            f"Impairments ({part_label})\n"
            f"Body system {sys_num}.00 {sys_title} (overview)\n"
        )
    text = f"{header}\n{body}".strip()
    return SourceDoc(
        source_id=citation,
        text=text,
        metadata={
            "jurisdiction": "US_Federal",
            "source_corpus": ECFRAdapter.source_corpus,
            "source_url": source_url,
            "citation": citation,
            "title": "20",
            "part": "404",
            "section": num,
            # The listings govern BOTH SSDI (Title II) and SSI (Title XVI, which
            # incorporates them by reference) disability determinations.
            "program": "Both",
            "effective_date": None,
            # Hierarchy carried through for future use / provenance (the reconciler
            # persists a fixed column set; these are also folded into `text`).
            "subpart": "P",
            "appendix": "1",
            "body_system": f"{sys_num}.00",
            "listing_part": part_letter,
            "retrieval_date": (retrieval_date or datetime.now(UTC)).isoformat(),
        },
    )


class ECFRAdapter(Adapter):
    source_corpus = "eCFR"
    purge_policy = PurgePolicy.DELETE

    def __init__(
        self,
        parts: list[int] | None = None,
        *,
        min_expected_docs: int = 300,
        timeout: float = 60.0,
        include_appendix: bool = True,
    ) -> None:
        # 404 = SSDI (Title II), 416 = SSI (Title XVI).
        self.parts = parts or [404, 416]
        # A healthy Title 20 Part 404 + 416 pull is several HUNDRED sections (the
        # prod corpus is ~1,623 sub-chunks across the two parts), plus ~240 Blue
        # Book listing/narrative docs when the appendix is included. A pull far
        # below this is a broken/partial fetch and must not be allowed to purge —
        # the systemic-fetch guard aborts the run instead (see reconciler). This
        # floor is conservative and TUNABLE: raise it toward the real observed
        # count once the first full ingest is measured. Adding the appendix only
        # RAISES the count, so the existing floor stays valid.
        self.min_expected_docs = min_expected_docs
        self._timeout = timeout
        # Blue Book = Appendix 1 to Subpart P of Part 404 (ingested once, under
        # 404). Appendix 2 (Medical-Vocational "grids") is table-structured and
        # deferred to a follow-up.
        self.include_appendix = include_appendix

    def _url(self, part: int) -> str:
        return (
            f"https://www.ecfr.gov/api/renderer/v1/content/enhanced/current"
            f"/title-20?chapter=III&part={part}"
        )

    def _appendix_url(self) -> str:
        # The renderer 302-redirects this to the canonical
        # ...&subpart=P&appendix=... URL; httpx follows it with the same headers.
        from urllib.parse import quote

        return (
            "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current"
            f"/title-20?chapter=III&part=404&appendix={quote(_APPENDIX_LABEL)}"
        )

    async def list_documents(self, mode: IngestionMode) -> Iterable[SourceDoc]:
        docs: list[SourceDoc] = []
        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=headers, follow_redirects=True
        ) as client:
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

            if self.include_appendix:
                app_url = self._appendix_url()
                logger.info("Fetching eCFR Blue Book (Part 404 Subpart P App. 1)...")
                resp = await client.get(app_url)
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"eCFR appendix fetch returned HTTP {resp.status_code}"
                    )
                app_docs = _parse_listing_appendix(
                    resp.text,
                    source_url=str(resp.url),
                    retrieval_date=datetime.now(UTC),
                )
                if not app_docs:
                    # An empty parse of a 200 body is a structural break, not a
                    # legitimately-empty appendix — treat as systemic so the
                    # reconciler aborts rather than purging the listings on a
                    # format change.
                    raise RuntimeError(
                        "eCFR appendix fetch parsed 0 listing docs "
                        "(source format change?)"
                    )
                docs.extend(app_docs)

        logger.info(
            "eCFR adapter yielded %d doc(s) (sections + appendix listings)", len(docs)
        )
        return docs
