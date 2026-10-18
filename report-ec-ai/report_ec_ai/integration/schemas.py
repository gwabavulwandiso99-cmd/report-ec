"""Typed request and response models shared by every part of the package.

These are the field names Member 5 (backend) works with. They are documented
with examples in docs/INTEGRATION.md. Keep the two in step.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from report_ec_ai.config import ADVISORY_NOTICE, MODEL_VERSION

Language = Literal["en", "xh"]
Channel = Literal["whatsapp", "ussd", "community_app", "test"]
Priority = Literal["low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Small shared pieces
# ---------------------------------------------------------------------------
class GeoPoint(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class MediaItem(StrictModel):
    """Metadata only. The photo itself stays with the channel/backend (e.g. a WhatsApp media id)."""
    media_id: str = Field(min_length=1, max_length=200)
    mime_type: str = Field(min_length=3, max_length=100)
    caption: Optional[str] = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Complaint analysis (Modules B and C)
# ---------------------------------------------------------------------------
class AnalyzeComplaintRequest(StrictModel):
    text: str = Field(max_length=4000)
    language_hint: Optional[Language] = None
    forced_category: Optional[str] = None
    forced_subcategory: Optional[str] = None


class LanguageInfo(BaseModel):
    detected: Literal["en", "xh", "mixed", "unknown"]
    confidence: float
    english_evidence: float
    xhosa_evidence: float


class MatchedTerm(BaseModel):
    concept: str
    text: str
    language: str
    method: str


class CategoryResult(BaseModel):
    category: str
    subcategory: str
    confidence: Confidence
    evidence_score: float = Field(description="0-1, how one-sided the evidence is. Not a probability.")
    explanation: str
    scores: dict
    legacy_category: str = Field(description="Value for the current dashboard/reports table")
    chosen_by_resident: bool = False


class PriorityResult(BaseModel):
    priority: Optional[Priority] = Field(description="None means not enough information to suggest one")
    confidence: Confidence
    explanation: str
    reasons: list[str]
    safety_indicators: list[str]
    safety_descriptions: list[str]
    duration_days: Optional[int] = None
    legacy_urgency: Optional[str] = None


class LocationHintsResult(BaseModel):
    area: Optional[str] = None
    municipality: Optional[str] = None
    landmark: Optional[str] = None
    landmark_words: Optional[str] = None
    ward: Optional[str] = None


class ComplaintAnalysis(BaseModel):
    original_text: str = Field(description="Exactly what the resident wrote. Never changed.")
    normalized_text: str
    corrections: list[list[str]] = Field(default_factory=list)
    language: LanguageInfo
    matched_terms: list[MatchedTerm]
    category: CategoryResult
    priority: PriorityResult
    location_hints: LocationHintsResult
    needs_clarification: bool
    clarification_reason: Optional[str] = None
    needs_human_review: bool
    review_reasons: list[str]
    instruction_like_text: bool = Field(
        default=False,
        description="True if the text looks like an instruction to the system. It is treated as plain complaint text.",
    )
    advisory_only: Literal[True] = True
    advisory_notice: str = ADVISORY_NOTICE
    model_version: str = MODEL_VERSION


# ---------------------------------------------------------------------------
# Chatbot (Module A)
# ---------------------------------------------------------------------------
class IncomingMessage(StrictModel):
    """One message from a resident, from any channel."""
    channel: Channel
    user_id: str = Field(min_length=1, max_length=128,
                         description="Channel user id, e.g. WhatsApp number or USSD phone number. Never stored by the AI as-is.")
    text: Optional[str] = Field(default=None, max_length=4096)
    message_id: Optional[str] = Field(default=None, max_length=200,
                                      description="Channel message id; repeated ids are ignored (webhook retries).")
    media: list[MediaItem] = Field(default_factory=list, max_length=10)
    location: Optional[GeoPoint] = None


class BotReply(BaseModel):
    messages: list[str]
    state: str
    language: Optional[Language] = None
    end_session: bool = Field(default=False, description="USSD: send END instead of CON")
    submitted_reference: Optional[str] = Field(
        default=None, description="Only set when the backend confirmed the submission")
    safety_notice: bool = False
    duplicate_message: bool = False


# ---------------------------------------------------------------------------
# Backend contract: what the chatbot sends to and expects from Member 5
# ---------------------------------------------------------------------------
class ReportLocation(BaseModel):
    municipality: Optional[str] = None
    municipality_source: Literal["resident", "recognised_area", "pilot_default"] = "pilot_default"
    area: Optional[str] = None
    landmark: Optional[str] = None
    location_text: Optional[str] = Field(default=None, description="Location exactly as the resident typed it")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    ward: Optional[str] = None
    needs_review: bool = False


class AISuggestion(BaseModel):
    category: str
    subcategory: str
    category_confidence: Confidence
    legacy_category: str
    priority: Optional[Priority]
    priority_confidence: Confidence
    legacy_urgency: Optional[str]
    explanation: str
    safety_indicators: list[str]
    needs_human_review: bool
    review_reasons: list[str]
    advisory_only: Literal[True] = True
    model_version: str = MODEL_VERSION


class ReportPayload(BaseModel):
    """A confirmed report, ready for the backend to store."""
    idempotency_key: str = Field(description="Same key = same report. The backend must not create a second report for it.")
    channel: Channel
    language: Language
    description_original: str
    description_source: Literal["resident_text", "ussd_menu"] = "resident_text"
    resident_selected_category: Optional[str] = None
    location: ReportLocation
    evidence: list[MediaItem] = Field(default_factory=list)
    ai_suggestion: AISuggestion
    created_at: datetime


class SubmissionResult(BaseModel):
    success: bool
    reference: Optional[str] = Field(default=None, max_length=40)
    status: Optional[str] = None
    error_code: Optional[str] = None
    retryable: bool = True
    message: Optional[str] = None


class StatusQuery(BaseModel):
    reference: str
    requester_id: str = Field(description="Channel user id of the person asking; the backend decides if they may see it")
    channel: Channel
    purpose: Literal["status", "verification"] = "status"


class StatusResult(BaseModel):
    found: bool
    authorized: bool = False
    reference: Optional[str] = None
    status: Optional[str] = None
    updated_at: Optional[datetime] = None
    municipal_note: Optional[str] = Field(default=None, max_length=500)
    resolution_claim: Optional[str] = Field(default=None, max_length=500,
                                            description="What the municipality said when it claimed the fix")


class VerificationPayload(BaseModel):
    idempotency_key: str
    reference: str
    channel: Channel
    language: Language
    stance_selected: Literal["confirm", "dispute", "unsure"]
    statement_original: Optional[str] = None
    evidence: list[MediaItem] = Field(default_factory=list)
    ai_advisory: "VerificationAnalysis"
    created_at: datetime


class VerificationSubmitResult(BaseModel):
    success: bool
    error_code: Optional[str] = None
    retryable: bool = True


# ---------------------------------------------------------------------------
# Duplicates (Module D)
# ---------------------------------------------------------------------------
class ReportForMatching(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reference: str
    description: str = Field(max_length=4000)
    category: Optional[str] = None
    subcategory: Optional[str] = None
    area: Optional[str] = None
    landmark: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    created_at: datetime
    status: Optional[str] = None


class DuplicateRequest(StrictModel):
    new_report: ReportForMatching
    existing_reports: list[ReportForMatching] = Field(max_length=2000)


class DuplicateCandidate(BaseModel):
    reference: str
    score: float
    label: Literal["strong_candidate", "possible_candidate"]
    relation: Literal["possible_same_incident", "possible_recurrence"]
    components: dict
    reasons: list[str]
    distance_m: Optional[float] = None
    hours_apart: float
    requires_human_confirmation: Literal[True] = True


class DuplicateResult(BaseModel):
    new_reference: str
    candidates: list[DuplicateCandidate]
    checked: int
    excluded: dict
    advisory_notice: str = (
        "Possible duplicates only. Reports are never merged, changed or deleted by the AI. "
        "Staff decide whether to link them."
    )
    model_version: str = MODEL_VERSION


# ---------------------------------------------------------------------------
# Verification triage (Module E)
# ---------------------------------------------------------------------------
class VerificationStatement(StrictModel):
    statement_id: Optional[str] = None
    text: Optional[str] = Field(default=None, max_length=2000)
    stance_selected: Optional[Literal["confirm", "dispute", "unsure"]] = None
    has_new_evidence: bool = False
    submitted_at: Optional[datetime] = None


class VerificationRequest(StrictModel):
    reference: Optional[str] = None
    municipal_claim: Optional[str] = Field(default=None, max_length=2000)
    statements: list[VerificationStatement] = Field(default_factory=list, max_length=500)


class StatementAssessment(BaseModel):
    statement_id: Optional[str]
    stance_from_text: Literal["confirms", "disputes", "unclear"]
    stance_selected: Optional[str]
    mentions_evidence: bool
    has_new_evidence: bool
    inconsistent_with_selection: bool
    instruction_like_text: bool
    cues: list[str]


class VerificationAnalysis(BaseModel):
    reference: Optional[str] = None
    claim_says_resolved: bool
    contradiction_detected: bool
    needs_human_review: bool
    counts: dict
    statements: list[StatementAssessment]
    explanation: str
    suggested_next_step: str
    uncertainty_note: str
    changes_status: Literal[False] = False
    advisory_only: Literal[True] = True
    advisory_notice: str = (
        "Advisory only. The AI does not decide whether a report is resolved. "
        "The authorised verification workflow sets the final status."
    )
    model_version: str = MODEL_VERSION


VerificationPayload.model_rebuild()


# ---------------------------------------------------------------------------
# Trends (Module F)
# ---------------------------------------------------------------------------
class TrendReport(BaseModel):
    """One report as input for trend analysis. Extra fields (phone, name...) are dropped on purpose."""
    model_config = ConfigDict(extra="ignore")
    reference: str
    category: str
    area: Optional[str] = None
    municipality: Optional[str] = None
    status: str
    created_at: datetime
    duplicate_of: Optional[str] = None
    dispute_count: int = Field(default=0, ge=0)

    @field_validator("category", "status")
    @classmethod
    def lower(cls, value: str) -> str:
        return value.strip().lower()


class TrendRequest(StrictModel):
    reports: list[TrendReport] = Field(max_length=20000)
    as_of: Optional[datetime] = None
    window_days: int = Field(default=30, ge=1, le=366)
    audience: Literal["public", "municipal"] = "public"
    data_label: str = Field(description="Say where the data came from, e.g. 'live' or 'SAMPLE DATA'")
