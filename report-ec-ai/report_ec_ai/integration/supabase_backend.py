"""The REAL backend adapter and session store, on the Report EC Supabase database.

Needs migration database/001_ai_chatbot.sql to have been run.

    from db import supabase                      # the backend's existing client
    from report_ec_ai.integration.supabase_backend import create_live_service
    ai = create_live_service(supabase)

Tables used (nothing else is touched):
    reports               insert new reports, read status (never changes a status)
    report_updates        read the latest message staff sent to residents
    report_verifications  insert resident confirm/dispute responses
    bot_sessions          chatbot conversation state (hashed key in "phone")

Works with the supabase-py client (or any client with the same .table() API).
The client must use the SECRET key, so this code must only ever run on the server.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from report_ec_ai.chatbot.prompts import t
from report_ec_ai.chatbot.session_store import Session, SessionStore, masked
from report_ec_ai.config import taxonomy
from report_ec_ai.integration.backend_adapter import BackendAdapter
from report_ec_ai.integration.schemas import (
    ReportForMatching,
    ReportPayload,
    StatusQuery,
    StatusResult,
    SubmissionResult,
    VerificationPayload,
    VerificationSubmitResult,
)
from report_ec_ai.intelligence.duplicates import DuplicateDetector

log = logging.getLogger("report_ec_ai.supabase")

# Values allowed by the CHECK constraints on public.reports today
DB_CATEGORIES = {"water_leak", "no_water", "power_outage", "pothole", "streetlight", "other"}
DB_URGENCIES = {"critical", "high", "medium"}
DB_CHANNELS = {"whatsapp", "ussd"}

SESSION_KEY_PATTERN = r"^(whatsapp|ussd|community_app|test):[0-9a-f]{32}$"
_MESSAGE_SENT = re.compile(r"Message sent:\s*[“\"](.*)[”\"]\s*$", re.DOTALL)


def phone_digits(phone: Optional[str]) -> str:
    """'whatsapp:+27 82 000 0001', '+27820000001' and '0820000001' all become '27820000001'."""
    digits = re.sub(r"\D", "", (phone or "").replace("whatsapp:", ""))
    if len(digits) == 10 and digits.startswith("0"):
        digits = "27" + digits[1:]
    return digits


def stored_phone(reporter_id: str) -> str:
    """How a phone number is saved in reports.phone (same style as before: no 'whatsapp:' prefix)."""
    return reporter_id.replace("whatsapp:", "").strip()


def is_unique_violation(error: Exception) -> bool:
    text = str(error)
    return getattr(error, "code", None) == "23505" or "23505" in text or "duplicate key" in text


def resident_message(update_text: Optional[str]) -> Optional[str]:
    """report_updates.text looks like 'In progress · Team · by Staff Name. Message sent: “...”'.
    Only the part in quotes was sent to residents, so only that part is shown to them."""
    if not update_text:
        return None
    found = _MESSAGE_SENT.search(update_text)
    return found.group(1).strip()[:500] if found else None


class SupabaseBackend(BackendAdapter):
    def __init__(self, client, open_verification: bool = False, suggest_duplicates: bool = True,
                 now: Optional[Callable[[], datetime]] = None):
        """open_verification: if True, any resident may confirm/dispute a fix. If False (default),
        only people who reported that issue (or a report linked to it) may. Team decision."""
        self.db = client
        self.open_verification = open_verification
        self.suggest_duplicates = suggest_duplicates
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.detector = DuplicateDetector()

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def _find_by_key(self, key: str):
        rows = self.db.table("reports").select("ref, status").eq("idempotency_key", key).limit(1).execute().data
        return rows[0] if rows else None

    def submit_report(self, payload: ReportPayload, reporter_id: str) -> SubmissionResult:
        existing = self._find_by_key(payload.idempotency_key)
        if existing:
            return SubmissionResult(success=True, reference=existing["ref"], status=existing["status"],
                                    message="Already received (same idempotency key)")
        row = self.report_row(payload, reporter_id)
        try:
            data = self.db.table("reports").insert(row).execute().data
        except Exception as error:
            if is_unique_violation(error):
                # Two copies of the same submission arrived at the same moment
                existing = self._find_by_key(payload.idempotency_key)
                if existing:
                    return SubmissionResult(success=True, reference=existing["ref"], status=existing["status"])
            log.warning("report insert failed: %s", type(error).__name__)
            return SubmissionResult(success=False, error_code="database_error", retryable=True)
        if not data or not data[0].get("ref"):
            return SubmissionResult(success=False, error_code="no_reference_returned", retryable=True)
        return SubmissionResult(success=True, reference=data[0]["ref"], status=data[0].get("status"))

    def report_row(self, payload: ReportPayload, reporter_id: str) -> dict:
        """Turns the chatbot payload into a row for public.reports (existing columns + migration 001)."""
        ai = payload.ai_suggestion
        loc = payload.location
        landmark = loc.location_text
        if loc.landmark and (not loc.location_text
                             or loc.location_text.strip().lower() == (loc.area or "").lower()):
            landmark = "Near the " + t("en", f"landmark.{loc.landmark}").lower()
        photo = next((m.media_id for m in payload.evidence if m.media_id.startswith("http")), None)

        suggestion = ai.model_dump(mode="json")
        suggestion.update({
            "description_source": payload.description_source,
            "resident_selected_category": payload.resident_selected_category,
            "location_needs_review": loc.needs_review,
            "municipality_source": loc.municipality_source,
            "landmark_id": loc.landmark,
            "evidence": [m.model_dump(mode="json") for m in payload.evidence],
            "possible_duplicates": self._possible_duplicates(payload) if self.suggest_duplicates else [],
        })
        return {
            "phone": stored_phone(reporter_id),
            "language": payload.language,
            "category": ai.legacy_category if ai.legacy_category in DB_CATEGORIES else "other",
            "description": payload.description_original,
            "photo_url": photo,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "landmark": landmark,
            "suburb": loc.area,
            "ward": loc.ward,
            "urgency": ai.legacy_urgency if ai.legacy_urgency in DB_URGENCIES else "medium",
            "status": "received",
            "channel": payload.channel if payload.channel in DB_CHANNELS else None,
            "idempotency_key": payload.idempotency_key,
            "location_text": loc.location_text,
            "municipality": loc.municipality,
            "ai_suggestion": suggestion,
        }

    def _possible_duplicates(self, payload: ReportPayload) -> list:
        """Suggestions only. Never sets duplicate_of: linking stays a staff action."""
        try:
            since = (self.now() - timedelta(days=14)).isoformat()
            rows = (self.db.table("reports")
                    .select("ref, description, category, suburb, latitude, longitude, created_at, status, ai_suggestion")
                    .gte("created_at", since).order("created_at", desc=True).limit(300).execute().data)
            legacy = taxonomy()["legacy_to_taxonomy"]
            existing = []
            for r in rows:
                category = (r.get("ai_suggestion") or {}).get("category")
                if not category and r.get("category") != "other":
                    category = legacy.get(r.get("category"), {}).get("category")
                existing.append(ReportForMatching(
                    reference=r["ref"], description=r.get("description") or "", category=category,
                    area=r.get("suburb"), latitude=r.get("latitude"), longitude=r.get("longitude"),
                    created_at=r["created_at"], status=r.get("status")))
            new = ReportForMatching(
                reference="NEW", description=payload.description_original,
                category=payload.ai_suggestion.category, subcategory=payload.ai_suggestion.subcategory,
                area=payload.location.area, landmark=payload.location.landmark,
                latitude=payload.location.latitude, longitude=payload.location.longitude,
                created_at=payload.created_at)
            result = self.detector.find(new, existing)
            return [{"ref": c.reference, "score": c.score, "label": c.label, "relation": c.relation,
                     "reasons": c.reasons} for c in result.candidates[:3]]
        except Exception as error:   # a duplicate check must never block a report
            log.warning("duplicate suggestion skipped: %s", type(error).__name__)
            return []

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def _group(self, report: dict) -> list:
        master = report.get("duplicate_of") or report["id"]
        return (self.db.table("reports").select("id, phone")
                .or_(f"id.eq.{master},duplicate_of.eq.{master}").execute().data)

    def get_report_status(self, query: StatusQuery) -> StatusResult:
        rows = (self.db.table("reports").select("id, ref, status, updated_at, duplicate_of")
                .eq("ref", query.reference.upper()).limit(1).execute().data)
        if not rows:
            return StatusResult(found=False)
        report = rows[0]
        group = self._group(report)
        requester = phone_digits(query.requester_id)
        reported_it = any(phone_digits(r.get("phone")) == requester for r in group)
        allowed = reported_it or (query.purpose == "verification" and self.open_verification)
        if not allowed:
            return StatusResult(found=False)     # same answer as "does not exist"

        master = report.get("duplicate_of") or report["id"]
        updates = (self.db.table("report_updates").select("text, created_at")
                   .eq("report_id", master).order("created_at", desc=True).limit(1).execute().data)
        latest = resident_message(updates[0]["text"]) if updates else None
        claimed_fixed = report["status"] == "resolved"
        return StatusResult(
            found=True, authorized=True, reference=report["ref"], status=report["status"],
            updated_at=report.get("updated_at"),
            municipal_note=None if claimed_fixed else latest,
            resolution_claim=latest if claimed_fixed else None,
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def submit_verification(self, payload: VerificationPayload, requester_id: str) -> VerificationSubmitResult:
        rows = (self.db.table("reports").select("id, duplicate_of")
                .eq("ref", payload.reference.upper()).limit(1).execute().data)
        if not rows:
            return VerificationSubmitResult(success=False, error_code="report_not_found", retryable=False)
        row = {
            "report_id": rows[0].get("duplicate_of") or rows[0]["id"],
            "phone": stored_phone(requester_id),
            "stance": payload.stance_selected,
            "statement": payload.statement_original,
            "evidence": [m.model_dump(mode="json") for m in payload.evidence],
            "ai_advisory": payload.ai_advisory.model_dump(mode="json"),
            "channel": payload.channel if payload.channel in DB_CHANNELS else None,
            "language": payload.language,
            "idempotency_key": payload.idempotency_key,
        }
        try:
            self.db.table("report_verifications").insert(row).execute()
        except Exception as error:
            if is_unique_violation(error):
                return VerificationSubmitResult(success=True)   # already saved earlier
            log.warning("verification insert failed: %s", type(error).__name__)
            return VerificationSubmitResult(success=False, error_code="database_error")
        # Deliberately NOT changing the report status here.
        return VerificationSubmitResult(success=True)


class SupabaseSessionStore(SessionStore):
    """Chatbot sessions in public.bot_sessions: phone = hashed session key, step = state, data = session."""

    def __init__(self, client, table: str = "bot_sessions", ttl_hours: int = 24,
                 now: Optional[Callable[[], datetime]] = None):
        self.db = client
        self.table = table
        self.ttl = timedelta(hours=ttl_hours)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def get(self, key):
        rows = self.db.table(self.table).select("data").eq("phone", key).limit(1).execute().data
        if not rows:
            return None
        try:
            session = Session.model_validate(rows[0]["data"])
        except Exception:
            log.warning("unreadable session dropped: %s", masked(key))
            self.delete(key)
            return None
        if self.now() - session.updated_at > self.ttl:
            self.delete(key)
            return None
        return session

    def save(self, session):
        self.db.table(self.table).upsert({
            "phone": session.key,
            "step": session.state.value,
            "data": session.model_dump(mode="json"),
            "updated_at": session.updated_at.isoformat(),
        }, on_conflict="phone").execute()

    def delete(self, key):
        self.db.table(self.table).delete().eq("phone", key).execute()

    def delete_expired(self) -> None:
        """Call once a day (e.g. from a scheduled job)."""
        cutoff = (self.now() - self.ttl).isoformat()
        # Only rows written by this store (hashed keys); anything else in the table is left alone
        (self.db.table(self.table).delete().lt("updated_at", cutoff)
         .filter("phone", "match", SESSION_KEY_PATTERN).execute())


def create_live_service(client, open_verification: bool = False):
    """ReportEcAIService wired to the real database (instead of the mock)."""
    from report_ec_ai.integration.service import ReportEcAIService

    return ReportEcAIService(
        backend=SupabaseBackend(client, open_verification=open_verification),
        session_store=SupabaseSessionStore(client),
    )
