"""Hermetic unit tests for ``_normalize_bm25_query``.

No DB, no AI — pure function. This normalizer fixes a confirmed retrieval bug: the
landing sample chip typed with CURLY quotes, ``What is the "five-step" evaluation?``
(U+201C/U+201D), mangled BM25 tokenization and made the flagship five-step question
DECLINE (it cited the wrong sections), while the identical PLAIN-ASCII question
retrieved 20 CFR § 416.920 and answered. The normalizer folds typographic punctuation
to ASCII and strips wrapping quotes so autocorrected input tokenizes like plain text.
"""

from app.services.knowledge_service import _normalize_bm25_query

# The literal chip query, with U+201C / U+201D curly quotes around "five-step".
_CURLY_FIVE_STEP = "What is the “five-step” evaluation?"


def test_curly_double_quotes_folded_and_stripped():
    """The exact curly-quote chip normalizes to the plain-ASCII form (quotes gone,
    hyphenated token intact) — matching the query that retrieves 416.920."""
    assert _normalize_bm25_query(_CURLY_FIVE_STEP) == "What is the five-step evaluation?"


def test_straight_wrapping_quotes_are_stripped():
    """Straight quotes wrapping a term are unwrapped so `"five-step"` → `five-step`."""
    assert _normalize_bm25_query('What is the "five-step" evaluation?') == (
        "What is the five-step evaluation?"
    )


def test_hyphen_inside_word_is_preserved():
    """Hyphens must NOT be stripped — `five-step` has to stay one matchable token."""
    assert _normalize_bm25_query("five-step sequential") == "five-step sequential"


def test_curly_apostrophe_folded_to_straight_and_kept_intra_word():
    """A curly apostrophe (U+2019) folds to a straight one and, being INSIDE the word,
    is preserved — `claimant's` is not mangled into `claimants`."""
    assert _normalize_bm25_query("claimant’s benefits") == "claimant's benefits"


def test_single_quotes_wrapping_a_token_are_stripped():
    """Single quotes that WRAP a token (curly or straight) are unwrapped."""
    assert _normalize_bm25_query("‘appeal’ deadline") == "appeal deadline"
    assert _normalize_bm25_query("'appeal' deadline") == "appeal deadline"


def test_whitespace_is_collapsed():
    """Runs of whitespace (incl. non-breaking space) collapse to single spaces and the
    result is trimmed."""
    assert _normalize_bm25_query("  five   step  ") == "five step"
    assert _normalize_bm25_query("five step\tevaluation") == "five step evaluation"


def test_dashes_folded_to_ascii_hyphen():
    """En/em dashes fold to an ASCII hyphen so a dash-joined term stays consistent."""
    assert _normalize_bm25_query("five–step") == "five-step"
    assert _normalize_bm25_query("five—step") == "five-step"


def test_plain_query_is_unchanged():
    """A clean ASCII question passes through untouched (aside from trimming)."""
    assert _normalize_bm25_query("What is the five-step evaluation?") == (
        "What is the five-step evaluation?"
    )


def test_citation_style_query_survives():
    """A bare citation query keeps its dotted form so the citation match still lands."""
    assert _normalize_bm25_query("404.1520") == "404.1520"


def test_empty_and_quote_only_inputs():
    """No content (empty, or only quote/whitespace chars) normalizes to empty string."""
    assert _normalize_bm25_query("") == ""
    assert _normalize_bm25_query("   ") == ""
    assert _normalize_bm25_query("“” '' \"\"") == ""
