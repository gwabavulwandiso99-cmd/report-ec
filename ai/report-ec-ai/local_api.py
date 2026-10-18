"""Runs the AI router on its own so teammates can try it in the browser (/docs).

Run (PowerShell):
    $env:REPORT_EC_AI_INTERNAL_KEY = "pick-a-long-random-value"
    uvicorn local_api:app --reload --port 8001

Then open http://127.0.0.1:8001/docs, click "Authorize"-style header fields
and send X-Internal-Key with the value above.

Uses the MOCK backend: no database, no WhatsApp. In the real system Member 5
mounts `create_router(...)` inside the main backend instead of running this file.
"""
import os
import secrets

from fastapi import FastAPI

from report_ec_ai.integration.backend_adapter import MockBackendAdapter
from report_ec_ai.integration.router import create_router
from report_ec_ai.integration.service import ReportEcAIService

key = os.getenv("REPORT_EC_AI_INTERNAL_KEY")
if not key:
    key = secrets.token_urlsafe(24)
    print(f"\nNo REPORT_EC_AI_INTERNAL_KEY set. Using this key for this run only:\n  {key}\n")

app = FastAPI(title="Report EC AI (local, mock backend)")
app.include_router(create_router(ReportEcAIService(backend=MockBackendAdapter()), internal_key=key))


@app.get("/")
def home():
    return {"service": "Report EC AI", "backend": "mock", "docs": "/docs"}
