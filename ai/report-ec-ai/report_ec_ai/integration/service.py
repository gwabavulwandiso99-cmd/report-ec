"""One object that gives Member 5 every AI feature through plain Python calls.

    from report_ec_ai.integration.service import ReportEcAIService
    ai = ReportEcAIService(backend=MyBackendAdapter())
    ai.process_message({...})        # chatbot
    ai.analyze_complaint("...")      # category + priority
    ai.suggest_duplicates(new, existing)
    ai.analyze_verification(request)
    ai.trend_summary(request)
"""
from typing import Optional

from report_ec_ai.chatbot.conversation import ConversationEngine
from report_ec_ai.chatbot.session_store import InMemorySessionStore, SessionStore
from report_ec_ai.chatbot.ussd import UssdGateway
from report_ec_ai.integration.backend_adapter import BackendAdapter, MockBackendAdapter
from report_ec_ai.integration.schemas import (
    AnalyzeComplaintRequest,
    BotReply,
    ComplaintAnalysis,
    DuplicateRequest,
    DuplicateResult,
    IncomingMessage,
    ReportForMatching,
    TrendRequest,
    VerificationAnalysis,
    VerificationRequest,
)
from report_ec_ai.intelligence.analyzer import ComplaintAnalyzer
from report_ec_ai.intelligence.duplicates import DuplicateDetector
from report_ec_ai.intelligence.trends import summarize_trends
from report_ec_ai.intelligence.verification import analyze_verification


class ReportEcAIService:
    def __init__(self, backend: Optional[BackendAdapter] = None,
                 session_store: Optional[SessionStore] = None,
                 analyzer: Optional[ComplaintAnalyzer] = None):
        self.backend = backend or MockBackendAdapter()
        self.analyzer = analyzer or ComplaintAnalyzer()
        self.engine = ConversationEngine(
            self.backend, session_store if session_store is not None else InMemorySessionStore(), self.analyzer)
        self.ussd = UssdGateway(self.engine)
        self.duplicates = DuplicateDetector(self.analyzer)

    @property
    def uses_mock_backend(self) -> bool:
        return isinstance(self.backend, MockBackendAdapter)

    def process_message(self, message) -> BotReply:
        return self.engine.handle(message if isinstance(message, IncomingMessage)
                                  else IncomingMessage.model_validate(message))

    def process_ussd(self, session_id: str, phone_number: str, text: str) -> str:
        return self.ussd.handle(session_id, phone_number, text)

    def analyze_complaint(self, request) -> ComplaintAnalysis:
        if isinstance(request, str):
            request = AnalyzeComplaintRequest(text=request)
        elif not isinstance(request, AnalyzeComplaintRequest):
            request = AnalyzeComplaintRequest.model_validate(request)
        return self.analyzer.analyze(request.text, request.language_hint,
                                     forced_category=request.forced_category,
                                     forced_subcategory=request.forced_subcategory)

    def suggest_duplicates(self, new_report, existing_reports=None) -> DuplicateResult:
        if isinstance(new_report, DuplicateRequest):
            request = new_report
        elif existing_reports is None:
            request = DuplicateRequest.model_validate(new_report)
        else:
            request = DuplicateRequest(
                new_report=ReportForMatching.model_validate(new_report),
                existing_reports=[ReportForMatching.model_validate(r) for r in existing_reports],
            )
        return self.duplicates.find(request.new_report, request.existing_reports)

    def analyze_verification(self, request) -> VerificationAnalysis:
        if not isinstance(request, VerificationRequest):
            request = VerificationRequest.model_validate(request)
        return analyze_verification(request)

    def trend_summary(self, request, include_ai_clusters: bool = False, descriptions: Optional[dict] = None) -> dict:
        """`descriptions` (municipal use only): {reference: {"description", "area", "created_at", ...}}
        lets the duplicate detector add unconfirmed AI clusters to the summary."""
        if not isinstance(request, TrendRequest):
            request = TrendRequest.model_validate(request)
        clusters = None
        if include_ai_clusters and descriptions:
            reports = [ReportForMatching.model_validate({"reference": ref, **data})
                       for ref, data in descriptions.items()]
            clusters = self.duplicates.clusters(reports)
        return summarize_trends(request.reports, request.data_label, request.as_of,
                                request.window_days, request.audience, clusters)
