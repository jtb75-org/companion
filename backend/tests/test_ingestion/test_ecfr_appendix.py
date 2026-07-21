"""Hermetic tests for the eCFR Blue Book (Appendix 1) listing parser + adapter.

Pure parse tests run off the captured fixture markup — NO network, NO DB, NO
embedding gateway (the ``stub_ai_backends`` autouse fixture covers AI seams; the
parser itself touches none). The one adapter test mocks ``httpx.AsyncClient.get``
so ``list_documents`` never leaves the process.
"""

import httpx
import pytest

from app.ingestion.adapters.ecfr import ECFRAdapter, _parse_listing_appendix
from app.ingestion.types import IngestionMode
from tests.test_ingestion.fixtures.ecfr_appendix1 import APPENDIX1_HTML

SOURCE_URL = "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-20"


@pytest.fixture
def docs():
    return _parse_listing_appendix(APPENDIX1_HTML, source_url=SOURCE_URL)


def _by_section(docs):
    return {d.metadata["section"]: d for d in docs}


# ── Segmentation: the right listings + narratives, and only those ───────────────


def test_yields_individual_listing_docs(docs):
    """One doc per individual impairment listing, keyed by its listing number."""
    by = _by_section(docs)
    for num in ("1.15", "1.16", "12.02", "12.04", "112.04"):
        assert num in by, f"missing listing {num}"


def test_yields_body_system_narrative_docs(docs):
    """Each body-system intro (N.00) becomes its own overview doc."""
    by = _by_section(docs)
    for num in ("1.00", "12.00", "112.00"):
        assert num in by, f"missing narrative {num}"
        assert by[num].text  # non-empty


def test_reserved_listings_are_skipped(docs):
    """A ``[Reserved]`` slot carries no criteria and must not become a doc."""
    assert "12.03" not in _by_section(docs)


def test_no_spurious_docs_and_no_empty_text(docs):
    """Exactly the expected doc set; no empty-text docs; the TOC rows + editorial
    banner + provenance box are all excluded."""
    by = _by_section(docs)
    assert set(by) == {
        "1.00", "1.15", "1.16",
        "12.00", "12.02", "12.04",
        "112.00", "112.04",
    }
    assert all(d.text.strip() for d in docs)


def test_banner_and_editorial_text_excluded(docs):
    """Provenance/banner/editorial container text never leaks into a doc."""
    joined = "\n".join(d.text for d in docs)
    assert "Published Edition" not in joined
    assert "Enhanced content" not in joined
    assert "last revised" not in joined


# ── Listing identity: stable, unique, collision-free citations ──────────────────


def test_source_ids_are_stable_unique_and_distinct_from_sections(docs):
    ids = [d.source_id for d in docs]
    assert len(ids) == len(set(ids))  # unique
    by = _by_section(docs)
    assert by["12.04"].source_id == "20 CFR Pt. 404, Subpt. P, App. 1, § 12.04"
    # The appendix citation form must never collide with the section form
    # ("20 CFR § 404.1520") the base adapter emits.
    assert all(s.source_id.startswith("20 CFR Pt. 404, Subpt. P, App. 1, § ")
               for s in docs)


def test_childhood_listing_distinct_from_adult(docs):
    """Part B's 112.04 is a separate doc/identity from Part A's 12.04."""
    by = _by_section(docs)
    assert by["12.04"].source_id != by["112.04"].source_id
    assert by["12.04"].metadata["listing_part"] == "A"
    assert by["112.04"].metadata["listing_part"] == "B"


# ── Metadata + hierarchy preservation ───────────────────────────────────────────


def test_metadata_matches_corpus_conventions(docs):
    d = _by_section(docs)["12.04"]
    m = d.metadata
    assert m["jurisdiction"] == "US_Federal"
    assert m["source_corpus"] == "eCFR"
    assert m["title"] == "20"
    assert m["part"] == "404"
    assert m["section"] == "12.04"
    # Listings govern both SSDI and SSI (416 incorporates them by reference).
    assert m["program"] == "Both"
    assert m["subpart"] == "P"
    assert m["appendix"] == "1"
    assert m["body_system"] == "12.00"
    assert m["source_url"] == SOURCE_URL
    assert m["effective_date"] is None
    assert m["retrieval_date"]  # ISO timestamp recorded


def test_hierarchy_and_criteria_preserved_in_text(docs):
    """The doc text pins Part, body system, listing, and keeps the full criteria —
    including a mid-criteria number that looks like a listing header."""
    text = _by_section(docs)["12.04"].text
    assert "Part A (Adults)" in text
    assert "Body system 12.00 Mental Disorders" in text
    assert "Listing 12.04 — Depressive, bipolar and related disorders" in text
    assert "Depressed mood" in text
    # The "9.57 ×" formula must stay INSIDE 12.04, not spawn a bogus 9.xx doc.
    assert "9.57" in text
    assert "9.57" not in {d.metadata["section"] for d in docs}


def test_narrative_carries_overview_marker(docs):
    d = _by_section(docs)["12.00"]
    assert "(overview)" in d.text
    assert "arranged in 11 categories" in d.text


# ── Guard: an unparseable body is systemic, not a legitimately-empty appendix ───


def test_empty_parse_is_reported_by_yielding_nothing():
    assert _parse_listing_appendix("<div>nothing here</div>", source_url=SOURCE_URL) == []


# ── Adapter wiring: appendix docs are appended to the section pull ──────────────


class _Resp:
    def __init__(self, text_body, url):
        self.text = text_body
        self.status_code = 200
        self.url = url


def _section_html(part):
    return (
        f'<div class="part" id="part-{part}">'
        f'<div class="section" id="{part}.1520">'
        f"<h4>§ {part}.1520 Evaluation of disability.</h4>"
        f"<p>Body for section {part}.1520.</p></div></div>"
    )


@pytest.mark.parametrize("include_appendix,expect_listing", [(True, True), (False, False)])
async def test_list_documents_appends_appendix(monkeypatch, include_appendix, expect_listing):
    async def mock_get(self, url, *args, **kwargs):
        if "appendix" in url:
            return _Resp(APPENDIX1_HTML, url)
        part = 404 if "part=404" in url else 416
        return _Resp(_section_html(part), url)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    adapter = ECFRAdapter(
        parts=[404, 416], min_expected_docs=1, include_appendix=include_appendix
    )
    docs = list(await adapter.list_documents(IngestionMode.RECONCILE))
    sids = {d.source_id for d in docs}

    # Section docs always present.
    assert "20 CFR § 404.1520" in sids
    assert "20 CFR § 416.1520" in sids
    # Listing docs present only when the appendix is enabled.
    listing_id = "20 CFR Pt. 404, Subpt. P, App. 1, § 12.04"
    assert (listing_id in sids) is expect_listing
