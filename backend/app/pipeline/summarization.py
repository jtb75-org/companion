"""Stage 4 — Generates plain-language summaries using LLM.

Uses Gemini to create spoken and card summaries at a 4th-6th grade reading level.
Prompts are configurable via Admin -> Prompts (system_config).
Falls back to templates if LLM is unavailable.
"""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_config import SystemConfig
from app.pipeline.schemas import (
    ClassificationResult,
    ExtractionResult,
    SummarizationResult,
)
from app.pipeline.text_complexity import get_flesch_kincaid_grade

logger = logging.getLogger(__name__)

_DEFAULT_SUMMARIZATION_PROMPT = (
    "You are a compassionate independence assistant for adults with developmental disabilities.\n"
    "Your goal is to summarize documents using the 'Easy Read' philosophy: "
    "simple words, clear actions, and no jargon.\n\n"
    "## INPUT DATA\n"
    "Classification: {classification}\n"
    "Urgency: {urgency}\n"
    "Extracted Data: {fields_json}\n\n"
    "## GUIDELINES\n"
    "- Reading Level: 4th-6th grade.\n"
    "- Tone: Warm, helpful, and reassuring.\n"
    "- Structure: 'What it is' followed by 'What to do'.\n"
    "- Safety: If the document is about money owed or medical news, "
    "stay calm and suggest a small next step.\n"
    "- CREDIT / ZERO BALANCE: If 'amount_due' is negative, zero, or the document "
    "shows a credit ('CREDIT', 'DO NOT PAY', a parenthesized or negative amount), "
    "the customer OWES NOTHING. NEVER tell them to pay, and never mention a due "
    "date to pay by. Instead say the account has a credit or that there is nothing "
    "to pay right now.\n\n"
    "## TASK\n"
    "1. Internal Reasoning: Briefly analyze the importance of this document.\n"
    "2. Spoken Summary: A 2-3 sentence friendly explanation for the user.\n"
    "3. Card Summary: A dashboard line (max 60 chars) in the format 'Sender — Key Detail'.\n\n"
    "## OUTPUT FORMAT\n"
    "Return ONLY valid JSON with these keys: 'reasoning', 'spoken', 'card'.\n"
    "Example (amount owed):\n"
    '{{'
    '  "reasoning": "This is a utility bill with a clear due date.",'
    '  "spoken": "You have a bill from the Electric Company '
    'for $45. You should pay it by next Friday.",'
    '  "card": "Electric Co — $45 due Friday"'
    '}}\n'
    "Example (credit — nothing owed, amount_due is -10.15):\n"
    '{{'
    '  "reasoning": "This statement is a credit, so nothing is owed.",'
    '  "spoken": "This statement from the City of Kirkwood shows a credit of '
    '$10.15. Your account is ahead, so you do not need to send any money.",'
    '  "card": "City of Kirkwood — $10.15 credit, all set"'
    '}}'
)


async def _get_summarization_prompt(db: AsyncSession | None) -> str:
    """Load summarization prompt from system_config, falling back to default."""
    if db is not None:
        try:
            async with db.begin_nested():
                result = await db.execute(
                    select(SystemConfig).where(
                        SystemConfig.category == "summarization_prompt",
                        SystemConfig.key == "default",
                        SystemConfig.is_active.is_(True),
                    )
                )
                config = result.scalar_one_or_none()
                if config and config.value and config.value.get("prompt"):
                    return config.value["prompt"]
        except Exception:
            logger.warning(
                "Failed to load summarization prompt, using default"
            )
    return _DEFAULT_SUMMARIZATION_PROMPT


async def summarize(
    classification: ClassificationResult,
    extraction: ExtractionResult,
    db: AsyncSession | None = None,
    ocr_confidence: float | None = None,
) -> SummarizationResult:
    """Generate plain-language spoken and card summaries.

    ``ocr_confidence`` is the OCR engine's mean recognition confidence (or None
    when unknown). When it is present and below the conservative review floor,
    the summary is forced into the review-inviting low-confidence tone even if
    the classification confidence is high — garbled OCR can produce a
    confident-but-wrong read, so we err toward a human look. Inert while OCR
    confidence is unavailable (see app.pipeline.confidence).
    """

    # Try LLM summarization
    llm_result = await _llm_summarize(
        classification, extraction, db
    )
    if llm_result is not None:
        spoken, card, reasoning = llm_result
    else:
        # Fallback to templates
        logger.warning(
            "LLM summarization failed for doc %s, using templates",
            classification.document_id,
        )
        summarizer = TEMPLATE_SUMMARIZERS.get(
            classification.classification, _template_generic
        )
        spoken, card = await summarizer(
            extraction.extracted_fields, classification
        )
        reasoning = "Fallback template used (LLM unavailable or failed)."

    urgency_label = {
        "routine": "Can Wait",
        "needs_attention": "Soon",
        "act_today": "Today",
        "urgent": "Today",
    }.get(classification.urgency_level, "Soon")

    # Apply confidence-based hedging (Trust Layer). A low *OCR* confidence pulls
    # the effective confidence down to the review-inviting band, so a garbled
    # scan never gets a falsely-direct summary just because the classifier was
    # sure. Erring toward review is the safe default for this audience.
    from app.pipeline.confidence import (
        CLASSIFY_SOFTEN_TONE,
        ocr_quality_forces_review,
    )
    effective_confidence = classification.confidence_score
    if ocr_quality_forces_review(ocr_confidence):
        effective_confidence = min(
            effective_confidence, CLASSIFY_SOFTEN_TONE - 0.01
        )
        logger.info(
            "OCR_QUALITY_REVIEW_FLOOR: doc=%s ocr_conf=%.3f class_conf=%.2f "
            "-> review-inviting summary tone",
            classification.document_id, ocr_confidence,
            classification.confidence_score,
        )
    spoken = _apply_confidence_hedging(spoken, effective_confidence)

    # Credit safety guard (defense-in-depth): never instruct a member to pay a
    # credit or zero balance, even if the LLM ignored the prompt guidance. This
    # may fully replace `spoken`, so the reading-grade check MUST run after it.
    spoken, card = _apply_credit_guard(
        spoken, card, classification, extraction.extracted_fields
    )

    # Reading complexity check — computed on the FINAL text the member receives
    # (post-hedging, post-credit-guard) so the audited grade matches reality.
    grade = get_flesch_kincaid_grade(spoken)
    if grade > 6.0:
        logger.warning(
            "COMPLEX_TEXT_WARNING: doc=%s grade=%.1f summary=%s",
            classification.document_id, grade, spoken
        )

    return SummarizationResult(
        document_id=classification.document_id,
        spoken_summary=spoken,
        card_summary=card,
        urgency_label=urgency_label,
        reasoning=reasoning,
        reading_grade=grade,
    )


def _apply_confidence_hedging(text: str, confidence: float) -> str:
    """Adjust the tone of the summary based on AI confidence."""
    # High confidence (>90%) -> Direct and factual
    if confidence >= 0.90:
        return text

    # Medium confidence (70-90%) -> Soften with "looks like"
    if confidence >= 0.70:
        prefixes = [
            "It looks like",
            "I think this is",
            "This seems to be",
        ]
        import random
        prefix = random.choice(prefixes)
        
        # Strip common starting phrases to avoid duplication
        clean_text = text
        for p in ["This is", "I found", "You have"]:
            if text.startswith(p):
                clean_text = text[len(p):].strip()
                break
        
        # Lowercase first letter if we added a prefix
        if clean_text[0].isupper() and not clean_text[0:2].isupper():
            clean_text = clean_text[0].lower() + clean_text[1:]
            
        return f"{prefix} {clean_text}"

    # Low confidence (<70%) -> Explicitly ask for review
    suffix = " I'm not 100% sure about this one — can we look at it together?"
    if text.endswith(".") or text.endswith("!"):
        return text[:-1] + suffix
    return text + suffix


def _coerce_amount(raw: object) -> float | None:
    """Best-effort convert an extracted amount_due into a float."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _apply_credit_guard(
    spoken: str,
    card: str,
    classification: ClassificationResult,
    fields: dict,
) -> tuple[str, str]:
    """Replace bill summaries for a credit/zero balance with credit-safe wording.

    Runs for any bill whose extracted amount_due is present and <= 0 (a credit or
    zero balance). The rewrite is UNCONDITIONAL — it does NOT try to detect
    payment-instruction language first. amount_due <= 0 definitively means the
    member owes nothing, and no keyword list can reliably catch every way an LLM
    might phrase a payment ("make a payment", "payment required", "send money",
    "remit", "mail a check"). Always rewriting is the only LLM-independent
    guarantee that a member is never told to pay a credit.

    When the classification confidence is below 0.90, the wording is
    COLLABORATIVE (invites review) rather than a flat statement, so an uncertain
    read never confidently suppresses a payment the member may actually owe.
    """
    if classification.classification != "bill":
        return spoken, card

    amount = _coerce_amount(fields.get("amount_due"))
    if amount is None or amount > 0:
        return spoken, card

    sender = fields.get("sender") or "this company"
    credit = abs(amount)
    low_confidence = classification.confidence_score < 0.90

    if low_confidence:
        # Uncertain read — invite a look together instead of a flat claim.
        if credit > 0:
            spoken = (
                f"I think this statement from {sender} shows a credit of "
                f"${credit:.2f} — want to look at it together?"
            )
            card = f"{sender} — maybe ${credit:.2f} credit, let's check"
        else:
            spoken = (
                f"I think this statement from {sender} shows a zero balance "
                "— want to look at it together?"
            )
            card = f"{sender} — maybe $0 balance, let's check"
    elif credit > 0:
        spoken = (
            f"This statement from {sender} shows a credit of ${credit:.2f}. "
            "Your account is ahead, so you do not need to send any money."
        )
        card = f"{sender} — ${credit:.2f} credit, all set"
    else:
        spoken = (
            f"This statement from {sender} shows a zero balance. "
            "Your account is all set, so you do not need to send any money "
            "right now."
        )
        card = f"{sender} — $0 balance, all set"

    logger.info(
        "CREDIT_GUARD_APPLIED: doc=%s amount_due=%s confidence=%.2f "
        "rewrote summary to credit-safe wording",
        classification.document_id, amount, classification.confidence_score,
    )
    return spoken, card


async def _llm_summarize(
    classification: ClassificationResult,
    extraction: ExtractionResult,
    db: AsyncSession | None,
) -> tuple[str, str, str] | None:
    """Use Gemini to generate summaries."""
    try:
        from app.conversation.llm import get_llm_client

        llm = get_llm_client()
        prompt_template = await _get_summarization_prompt(db)

        fields_json = json.dumps(
            extraction.extracted_fields, indent=2, default=str
        )
        prompt = prompt_template.format(
            classification=classification.classification,
            urgency=classification.urgency_level,
            fields_json=fields_json,
        )

        response = await llm.generate(
            system_prompt=(
                "You are a friendly document summarizer. "
                "Return ONLY valid JSON, no other text."
            ),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.3,
            response_json=True,
            disable_thinking=False,
        )

        from app.conversation.llm import extract_json

        parsed = extract_json(response)
        reasoning = parsed.get("reasoning", "").strip()
        spoken = parsed.get("spoken", "").strip()
        card = parsed.get("card", "").strip()

        if spoken and card:
            logger.info(
                "LLM_SUMMARIZE_REASONING: doc=%s reasoning=%s",
                classification.document_id, reasoning,
            )
            return spoken, card, reasoning

        logger.warning("LLM summarization returned empty fields")
        return None
    except (json.JSONDecodeError, ValueError) as e:
        raw = response[:500] if response else "empty"
        logger.warning(
            "LLM summarization JSON parse failed: %s — raw: %s",
            e, raw,
        )
        return None
    except Exception:
        logger.exception("LLM summarization failed")
        return None


# ── Template fallbacks ──


async def _template_bill(
    fields: dict, classification: ClassificationResult
) -> tuple[str, str]:
    sender = fields.get("sender", "Unknown sender")
    amount = fields.get("amount_due")
    due_date = fields.get("due_date")

    if amount and due_date:
        spoken = (
            f"This is a bill from {sender}. "
            f"You owe ${amount} and it's due {due_date}."
        )
        card = f"{sender} — ${amount} due {due_date}"
    elif amount:
        spoken = f"This is a bill from {sender}. You owe ${amount}."
        card = f"{sender} — ${amount}"
    else:
        spoken = (
            f"This looks like a bill from {sender}. "
            "I couldn't find the amount."
        )
        card = f"{sender} — amount unclear"

    return spoken, card


async def _template_medical(
    fields: dict, classification: ClassificationResult
) -> tuple[str, str]:
    provider = fields.get("provider") or "your doctor"
    notice = fields.get("nature_of_notice")
    action = fields.get("required_action")
    date_time = fields.get("date_time")

    if notice and action:
        spoken = f"This is a {notice} notice from {provider}. It says you should {action}."
        card = f"{provider} — {notice}"
    elif notice:
        spoken = f"This is a {notice} notice from {provider}."
        card = f"{provider} — {notice}"
    elif date_time:
        spoken = f"You have an appointment with {provider} on {date_time}."
        card = f"{provider} — {date_time}"
    else:
        spoken = f"This is a medical document from {provider}."
        card = f"{provider} — medical document"

    return spoken, card


async def _template_legal(
    fields: dict, classification: ClassificationResult
) -> tuple[str, str]:
    sender = fields.get("sender") or "someone"
    notice = fields.get("nature_of_notice") or "legal"
    action = fields.get("required_action")
    deadline = fields.get("response_deadline")

    spoken = f"This is a {notice} notice from {sender}."
    if action:
        spoken += f" It says you need to {action}."
    if deadline:
        spoken += f" There is a deadline of {deadline}."
    
    spoken += " You should look at this with a trusted contact."
    card = f"{notice.capitalize()} from {sender}"

    return spoken, card


async def _template_junk(
    fields: dict, classification: ClassificationResult
) -> tuple[str, str]:
    return (
        "This looks like junk mail. I'll set it aside for you.",
        "Junk mail — no action needed",
    )


async def _template_generic(
    fields: dict, classification: ClassificationResult
) -> tuple[str, str]:
    sender = fields.get("sender") or "someone"
    notice = fields.get("nature_of_notice") or "document"
    action = fields.get("required_action")

    if action:
        spoken = f"I found a {notice} from {sender}. It says you should {action}."
    else:
        spoken = (
            f"I found a {notice} from {sender}. "
            "I'm not sure if you need to do anything yet."
        )
    
    card = f"{notice.capitalize()} from {sender}"

    return spoken, card


TEMPLATE_SUMMARIZERS: dict[str, object] = {
    "bill": _template_bill,
    "medical": _template_medical,
    "legal": _template_legal,
    "junk": _template_junk,
}
