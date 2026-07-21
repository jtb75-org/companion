"""Hermetic unit tests for Reciprocal Rank Fusion (RRF).

No DB, no AI — pure function. RRF is the fusion step of the hybrid BM25 + vector
retrieval in ``knowledge_service.search_regulations``: it merges the two ranked
result lists into one, consuming only RANKS (so BM25 and cosine scores never need
to be normalized against each other).
"""

from app.services.knowledge_service import _RRF_K, reciprocal_rank_fusion


def test_fuses_two_lists_in_correct_order():
    """A key ranked in BOTH lists outranks keys ranked in only one.

    lists = [[a,b,c], [b,c,d]] with k=60:
      a = 1/61                 = 0.016393
      b = 1/62 + 1/61          = 0.032523   (best overall — in both, high in both)
      c = 1/63 + 1/62          = 0.031997
      d = 1/63                 = 0.015873
    → b, c, a, d
    """
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "d"]], k=60)
    assert fused == ["b", "c", "a", "d"]


def test_limit_truncates_fused_result():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "d"]], k=60, limit=2)
    assert fused == ["b", "c"]


def test_single_list_preserves_order():
    """One leg only (e.g. BM25 unavailable) → RRF is order-preserving identity."""
    assert reciprocal_rank_fusion([["x", "y", "z"]]) == ["x", "y", "z"]


def test_empty_inputs():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_tie_breaks_by_first_appearance_deterministically():
    """Two keys each ranked #1 in a different list tie on score; the tie breaks by
    first appearance across the input lists (left list first), so fusion is stable."""
    assert reciprocal_rank_fusion([["a"], ["b"]]) == ["a", "b"]
    # Order of the input lists decides the tie-break.
    assert reciprocal_rank_fusion([["b"], ["a"]]) == ["b", "a"]


def test_flagship_five_step_shape():
    """The retrieval shape behind the live regression: the semantic leg missed the
    correct section (it is not in that list); the lexical (BM25) leg ranks it #1.
    RRF must lift it into the fused top results even though only one leg found it.

    vector leg (semantic): [416.1407, 416.924, 404.967]  (tangential, no 404.1520)
    bm25 leg   (lexical):  [404.1520, 404.1505]           (five-step match #1)
    """
    vector = ["416.1407", "416.924", "404.967"]
    bm25 = ["404.1520", "404.1505"]
    fused = reciprocal_rank_fusion([vector, bm25], limit=5)
    # 404.1520 ties the top semantic hit on score (both rank #1) and, being rank #1
    # in BM25, lands in the fused top-2 — where vector-only never surfaced it at all.
    assert "404.1520" in fused[:2]
    assert "404.1520" in fused


def test_duplicate_in_a_list_uses_best_rank():
    """A key repeated inside one list is scored once, at its best (first) rank."""
    fused = reciprocal_rank_fusion([["a", "a", "b"]])
    assert fused == ["a", "b"]


def test_default_k_is_canonical_60():
    assert _RRF_K == 60
