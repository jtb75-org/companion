"""Confidence tiers and the OCR-confidence log-and-observe plan.

This module is the single documented home for the confidence thresholds that
shape what a member sees, and for the honest finding behind the "recalibrate
OCR confidence tiers for PaddleOCR primary" pre-PHI gate.

WHICH confidence drives member-facing behavior today
-----------------------------------------------------
The score that flows through the pipeline into member-facing wording is the
**classification confidence** (``ClassificationResult.confidence_score``),
produced in :mod:`app.pipeline.classification`:

* Tier 1 (rule-based): ``min(0.5 + 0.12 * pattern_matches, 1.0)`` — a function
  of how many regexes matched the *already-extracted text*.
* Tier 2 (LLM): the model's self-reported ``confidence`` (Gemini/Vertex),
  clamped to [0, 1], default 0.7; fallbacks 0.95 (heuristic retry) / 0.3
  (unknown).

It gates:

* Classification tiering (:mod:`app.pipeline.classification`): accept Tier 1
  only if ``> 0.95``; ``junk`` needs ``>= 0.90``; Tier 1 needs ``> 0.70``.
* Member-facing summary hedging (:func:`app.pipeline.summarization`): ``>= 0.90``
  states facts directly; ``0.70–0.90`` softens ("It looks like…"); ``< 0.70``
  invites review ("I'm not 100% sure — can we look at it together?").
* The credit-guard tone (:mod:`app.pipeline.summarization`): ``< 0.90`` uses
  collaborative "want to look at it together?" wording.

Note: routing to ``pending_review`` vs ``auto_created`` is decided by the
member's **care model** (self-directed always reviews; managed auto-creates with
an audit-trail review) and document type — NOT by a confidence threshold.
Confidence is *stored* on ``PendingReview`` but does not currently branch
routing.

Why there were no "OCR confidence tiers" to recalibrate
-------------------------------------------------------
The classification confidence above is computed on the **extracted text** and is
therefore **OCR-engine-agnostic**: swapping Document AI → PaddleOCR does not, by
construction, shift its distribution. And the OCR engines' own recognition
confidence was **never captured** — ``OcrResult`` carried only ``(text,
provider, ms)``, the PaddleOCR HTTP service returned ``{"text", "ms"}`` (it read
``det[1][0]`` and discarded the ``det[1][1]`` score), and the Document AI
provider returned only ``document.text``. So there is no pre-existing OCR-tuned
threshold that PaddleOCR's different score semantics could have misaligned.

The real PaddleOCR-primary risk is different and is what this gate should track:
PaddleOCR degrades on poor scans (2026-06-20 benchmark: 0.767 vs Document AI's
0.937 on degraded captures). Garbled OCR text can still yield a *confident*
classification/extraction (the LLM cannot tell the text is garbled) → a
confident-but-wrong member summary. Nothing today lets low OCR quality pull a
document toward review.

What this change does
---------------------
1. Captures the OCR engines' native confidence (PaddleOCR per-line scores,
   Document AI per-token ``layout.confidence``) that both were discarding, and
   threads it — as pure telemetry — into ``pipeline_metrics`` and the ingestion
   log, so a *real* OCR-confidence distribution can be built on real documents.
2. Adds a conservative, **inert-by-default** OCR-quality review floor
   (:data:`OCR_CONFIDENCE_REVIEW_FLOOR`): when — and only when — a real OCR
   confidence is present AND below the floor, the member gets the
   review-inviting summary tone even if the classification confidence is high
   (err toward human review). Until the deployed OCR service ships the
   ``confidence`` field, ``ocr_confidence`` is ``None`` and the floor is a
   no-op, so no un-tuned number changes what any member sees.

Real-data tuning is still required
----------------------------------
The numeric bands below are the EXISTING classification thresholds (unchanged —
they are not OCR-tuned and there is no evidence to move them) plus a
deliberately conservative OCR floor. They are NOT fitted to a real distribution,
because none exists yet (prod has essentially no member OCR volume). Once the
telemetry has accumulated real PaddleOCR confidences across the D.D. document
mix, revisit :data:`OCR_CONFIDENCE_REVIEW_FLOOR` (and consider a distinct
auto-file ceiling) against that distribution. Erring toward more review is the
safe default for an elderly/IDD-facing health app: a doc wrongly auto-filed is
worse than one wrongly sent to review.
"""

from __future__ import annotations

# --- Classification-confidence tiers (member-facing wording) -----------------
# These mirror the literals used in classification.py / summarization.py. They
# are documented here as the source of truth; the modules keep their inline
# values to avoid a churny cross-module refactor in the same PR that ships the
# telemetry. Keep them in sync.
CLASSIFY_DIRECT_TONE = 0.90      # >= : state facts plainly
CLASSIFY_SOFTEN_TONE = 0.70      # >= : "It looks like…"
# < CLASSIFY_SOFTEN_TONE          : explicitly invite review

# --- OCR-quality review floor (log-and-observe; conservative) -----------------
# When an OCR engine reports a mean confidence at or below this floor, treat the
# document's summary as low-confidence (review-inviting tone) regardless of the
# classification confidence. Conservative starting point pending real-data
# tuning; only active when a real ``ocr_confidence`` is present.
OCR_CONFIDENCE_REVIEW_FLOOR = 0.80


def ocr_quality_forces_review(ocr_confidence: float | None) -> bool:
    """True when a *known* OCR confidence is low enough to force review tone.

    ``None`` (engine/service reported nothing) is treated as "no signal" and
    NEVER forces review on its own — absence of telemetry must not degrade the
    experience. This keeps the floor inert until the OCR service ships real
    confidences and the floor is validated against them.
    """
    return ocr_confidence is not None and ocr_confidence <= OCR_CONFIDENCE_REVIEW_FLOOR
