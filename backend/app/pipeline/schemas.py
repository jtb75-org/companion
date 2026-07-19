from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class NormalizedDocument(BaseModel):
    """Output of Stage 1 — the unified format both input channels normalize to."""
    document_id: UUID
    user_id: UUID
    source_channel: str  # camera_scan, email
    raw_text: str
    metadata: dict = Field(default_factory=dict)
    quality_score: float = 1.0
    # Mean OCR recognition confidence in [0, 1] the engine reported, or None
    # when it reported none (older service builds, or the email channel). Pure
    # telemetry today; see app.pipeline.confidence.
    ocr_confidence: float | None = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


class ClassificationResult(BaseModel):
    """Output of Stage 2."""
    document_id: UUID
    # bill, legal, government, medical, insurance, form, junk, personal, unknown
    classification: str
    urgency_level: str  # routine, needs_attention, act_today, urgent
    confidence_score: float = Field(ge=0.0, le=1.0)
    classifier_tier: int = 1  # 1=fast rule-based, 2=LLM


class ExtractionResult(BaseModel):
    """Output of Stage 3."""
    document_id: UUID
    extracted_fields: dict  # varies by classification type
    missing_fields: list[str] = Field(default_factory=list)
    needs_user_input: bool = False


# Per-type extraction schemas
class BillExtraction(BaseModel):
    sender: str | None = None
    account_number_masked: str | None = None  # last 4 only
    amount_due: Decimal | None = None
    due_date: str | None = None  # ISO date
    minimum_payment: Decimal | None = None
    late_fee: Decimal | None = None
    payment_methods: list[str] = Field(default_factory=list)

class MedicalDocumentExtraction(BaseModel):
    provider: str | None = None
    date_time: str | None = None
    location: str | None = None
    preparation_instructions: str | None = None
    contact_number: str | None = None
    nature_of_notice: str | None = None  # e.g., Retirement, Clinic Move, Result
    required_action: str | None = None  # e.g., Find new doctor, Call office

class LegalExtraction(BaseModel):
    sender: str | None = None
    nature_of_notice: str | None = None
    response_deadline: str | None = None
    required_action: str | None = None
    contact_info: str | None = None

class FormExtraction(BaseModel):
    title: str | None = None
    issuing_org: str | None = None
    purpose: str | None = None
    submission_deadline: str | None = None
    required_fields: list[str] = Field(default_factory=list)

class GovernmentExtraction(BaseModel):
    agency: str | None = None
    document_type: str | None = None
    action_required: str | None = None
    deadline: str | None = None
    reference_number: str | None = None

class InsuranceExtraction(BaseModel):
    provider: str | None = None
    policy_number: str | None = None
    claim_amount: Decimal | None = None
    patient_responsibility: Decimal | None = None
    date_of_service: str | None = None


EXTRACTION_SCHEMAS: dict[str, type[BaseModel]] = {
    "bill": BillExtraction,
    "medical": MedicalDocumentExtraction,
    "legal": LegalExtraction,
    "form": FormExtraction,
    "government": GovernmentExtraction,
    "insurance": InsuranceExtraction,
}


class SummarizationResult(BaseModel):
    """Output of Stage 4."""
    document_id: UUID
    spoken_summary: str  # plain language, max 3 sentences
    card_summary: str  # short text for UI card
    urgency_label: str  # Today, Soon, Can Wait
    reasoning: str | None = None  # LLM's internal reasoning for the summary
    reading_grade: float = 0.0  # Flesch-Kincaid Grade Level


class RoutingResult(BaseModel):
    """Output of Stage 5."""
    document_id: UUID
    routing_destination: str  # home, my_health, bills, plans
    suggested_action: str | None = None  # verb + object + reason
    records_created: list[dict] = Field(default_factory=list)
    pending_review_id: UUID | None = None


class PipelineResult(BaseModel):
    """Complete pipeline output combining all stages."""
    document_id: UUID
    classification: ClassificationResult
    extraction: ExtractionResult
    summarization: SummarizationResult
    routing: RoutingResult
    processing_time_ms: int = 0
