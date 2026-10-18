"""The contract between the chatbot and the main Report EC backend (Member 5).

The chatbot never talks to Supabase directly. It calls a BackendAdapter.
Member 5 provides the real one; MockBackendAdapter is for demos and tests.

Rules every adapter must follow:
- submit_report: create the report ONCE per idempotency_key. If the same key
  arrives again, return the SAME reference instead of creating a second report.
  Return success=False (never a made-up reference) if the report was not saved.
- get_report_status: decide whether requester_id may see the report. If not,
  return found=False (do not reveal that it exists).
- submit_verification: store the resident's response once per idempotency_key.
  It must NOT change the report status by itself; the verification workflow does.
"""
import itertools
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable, Optional

from report_ec_ai.integration.schemas import (
    ReportPayload,
    StatusQuery,
    StatusResult,
    SubmissionResult,
    VerificationPayload,
    VerificationSubmitResult,
)


class BackendError(Exception):
    """Raised by an adapter when the backend cannot be reached."""


class BackendAdapter(ABC):
    @abstractmethod
    def submit_report(self, payload: ReportPayload, reporter_id: str) -> SubmissionResult:
        ...

    @abstractmethod
    def get_report_status(self, query: StatusQuery) -> StatusResult:
        ...

    @abstractmethod
    def submit_verification(self, payload: VerificationPayload, requester_id: str) -> VerificationSubmitResult:
        ...


class CallbackBackendAdapter(BackendAdapter):
    """Lets Member 5 plug in three plain functions without writing a class."""

    def __init__(self, submit_report: Callable, get_report_status: Callable, submit_verification: Callable):
        self._submit = submit_report
        self._status = get_report_status
        self._verify = submit_verification

    def submit_report(self, payload, reporter_id):
        return SubmissionResult.model_validate(self._submit(payload, reporter_id))

    def get_report_status(self, query):
        return StatusResult.model_validate(self._status(query))

    def submit_verification(self, payload, requester_id):
        return VerificationSubmitResult.model_validate(self._verify(payload, requester_id))


class MockBackendAdapter(BackendAdapter):
    """In-memory stand-in for the real backend. NOT connected to any database.

    References look like MOCK-EC-1001 so a mock reference can never be confused
    with a real one. Use `fail_next_submissions` / `offline` to test failures.
    """

    def __init__(self, start_number: int = 1001):
        self._numbers = itertools.count(start_number)
        self._lock = threading.Lock()
        self.reports = {}            # reference -> {"payload", "reporter_id", "status", ...}
        self.by_key = {}             # idempotency_key -> reference
        self.verifications = {}      # idempotency_key -> {"payload", "requester_id"}
        self.submit_calls = 0
        self.fail_next_submissions = 0
        self.fail_next_verifications = 0
        self.return_success_without_reference = False
        self.offline = False

    # --- helpers for demos/tests ---
    def add_report(self, reference: str, reporter_id: str, status: str,
                   resolution_claim: Optional[str] = None, municipal_note: Optional[str] = None):
        self.reports[reference] = {
            "payload": None, "reporter_id": reporter_id, "status": status,
            "resolution_claim": resolution_claim, "municipal_note": municipal_note,
            "updated_at": datetime.now(timezone.utc),
        }

    def set_status(self, reference: str, status: str, resolution_claim: Optional[str] = None):
        self.reports[reference]["status"] = status
        self.reports[reference]["updated_at"] = datetime.now(timezone.utc)
        if resolution_claim is not None:
            self.reports[reference]["resolution_claim"] = resolution_claim

    # --- the contract ---
    def submit_report(self, payload: ReportPayload, reporter_id: str) -> SubmissionResult:
        with self._lock:
            self.submit_calls += 1
            if self.offline:
                raise BackendError("Mock backend is offline")
            if self.fail_next_submissions > 0:
                self.fail_next_submissions -= 1
                return SubmissionResult(success=False, error_code="mock_failure", retryable=True,
                                        message="Simulated failure")
            if self.return_success_without_reference:
                return SubmissionResult(success=True, reference=None)
            if payload.idempotency_key in self.by_key:
                reference = self.by_key[payload.idempotency_key]
                return SubmissionResult(success=True, reference=reference, status="submitted",
                                        message="Already received (same idempotency key)")
            reference = f"MOCK-EC-{next(self._numbers)}"
            self.by_key[payload.idempotency_key] = reference
            self.reports[reference] = {
                "payload": payload, "reporter_id": reporter_id, "status": "submitted",
                "resolution_claim": None, "municipal_note": None,
                "updated_at": datetime.now(timezone.utc),
            }
            return SubmissionResult(success=True, reference=reference, status="submitted")

    def get_report_status(self, query: StatusQuery) -> StatusResult:
        if self.offline:
            raise BackendError("Mock backend is offline")
        report = self.reports.get(query.reference.upper())
        if report is None:
            return StatusResult(found=False)
        # Mock rule: only the original reporter may check status. For verification,
        # any resident may respond (Member 5 decides the real rule).
        allowed = query.purpose == "verification" or report["reporter_id"] == query.requester_id
        if not allowed:
            return StatusResult(found=False)
        return StatusResult(
            found=True, authorized=True, reference=query.reference.upper(),
            status=report["status"], updated_at=report["updated_at"],
            municipal_note=report["municipal_note"], resolution_claim=report["resolution_claim"],
        )

    def submit_verification(self, payload: VerificationPayload, requester_id: str) -> VerificationSubmitResult:
        if self.offline:
            raise BackendError("Mock backend is offline")
        if self.fail_next_verifications > 0:
            self.fail_next_verifications -= 1
            return VerificationSubmitResult(success=False, error_code="mock_failure")
        # Stored once per key; the report status is NOT changed here on purpose
        self.verifications.setdefault(payload.idempotency_key,
                                      {"payload": payload, "requester_id": requester_id})
        return VerificationSubmitResult(success=True)
