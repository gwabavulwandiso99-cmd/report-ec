"""Optional FastAPI router for the AI service.

These endpoints are INTERNAL: the main Report EC backend calls them, residents
and browsers do not. Every request must send the header

    X-Internal-Key: <value of REPORT_EC_AI_INTERNAL_KEY>

If no key is configured, every endpoint answers 503 (fail closed), so a
forgotten setting can never leave the service open.

Mount it in the main backend:

    from report_ec_ai.integration.router import create_router
    app.include_router(create_router(ai_service))
"""
import hmac
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from report_ec_ai.integration.schemas import (
    AnalyzeComplaintRequest,
    BotReply,
    ComplaintAnalysis,
    DuplicateRequest,
    DuplicateResult,
    IncomingMessage,
    TrendRequest,
    VerificationAnalysis,
    VerificationRequest,
)
from report_ec_ai.integration.service import ReportEcAIService


class UssdRequest(BaseModel):
    session_id: str = Field(max_length=200)
    phone_number: str = Field(max_length=32)
    text: str = Field(default="", max_length=1000)


def create_router(service: ReportEcAIService, internal_key: Optional[str] = None,
                  prefix: str = "/ai") -> APIRouter:
    configured_key = internal_key or os.getenv("REPORT_EC_AI_INTERNAL_KEY")

    def require_internal_key(x_internal_key: Optional[str] = Header(default=None)):
        if not configured_key:
            raise HTTPException(status_code=503, detail="AI service key is not configured")
        if not x_internal_key or not hmac.compare_digest(x_internal_key, configured_key):
            raise HTTPException(status_code=401, detail="Missing or wrong X-Internal-Key")

    router = APIRouter(prefix=prefix, tags=["Report EC AI"], dependencies=[Depends(require_internal_key)])

    @router.get("/health")
    def health():
        return {"ok": True, "mock_backend": service.uses_mock_backend}

    @router.post("/chat/message", response_model=BotReply)
    def chat_message(message: IncomingMessage):
        return service.process_message(message)

    @router.post("/chat/ussd", response_class=PlainTextResponse)
    def chat_ussd(request: UssdRequest):
        return service.process_ussd(request.session_id, request.phone_number, request.text)

    @router.post("/complaints/analyze", response_model=ComplaintAnalysis)
    def analyze(request: AnalyzeComplaintRequest):
        try:
            return service.analyze_complaint(request)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))

    @router.post("/complaints/duplicates", response_model=DuplicateResult)
    def duplicates(request: DuplicateRequest):
        return service.suggest_duplicates(request)

    @router.post("/verification/analyze", response_model=VerificationAnalysis)
    def verification(request: VerificationRequest):
        return service.analyze_verification(request)

    @router.post("/trends/summary")
    def trends(request: TrendRequest):
        return service.trend_summary(request)

    return router
