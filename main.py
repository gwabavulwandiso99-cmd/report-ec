import base64
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import supabase
from notify import send_whatsapp

load_dotenv()

app = FastAPI(title="Report EC")

# ---------------------------------------------------------------------------
# Login. The dashboard shows exact locations, photos and resident messages, and
# PATCH sends WhatsApp messages, so none of it may be open to the internet.
# The browser shows a login box and then remembers it for this site.
# Use HTTPS when this is deployed: Basic auth sends the password in clear text.
# ---------------------------------------------------------------------------
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
if not DASHBOARD_PASSWORD:
    raise RuntimeError("DASHBOARD_PASSWORD is missing from .env")

PROTECTED_PREFIXES = ("/dashboard", "/reports")


@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path.startswith(PROTECTED_PREFIXES):
        allowed = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, password = base64.b64decode(header[6:]).decode().partition(":")
                allowed = secrets.compare_digest(user.encode(), DASHBOARD_USER.encode()) and \
                    secrets.compare_digest(password.encode(), DASHBOARD_PASSWORD.encode())
            except Exception:
                allowed = False
        if not allowed:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Report EC"'})
    return await call_next(request)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
# The resident's phone number is left out on purpose: the dashboard tracks the
# issue, not the person.
DASHBOARD_COLUMNS = (
    "id, ref, language, category, description, photo_url, latitude, longitude, "
    "landmark, suburb, ward, urgency, status, team, duplicate_of, created_at, "
    "updated_at, first_response_at, resolved_at"
)

STATUS_LABELS = {
    "received": "Received",
    "awaiting_dispatch": "Awaiting dispatch",
    "team_assigned": "Team assigned",
    "in_progress": "In progress",
    "resolved": "Resolved",
}


def ward_number(ward: Optional[str]) -> Optional[str]:
    """'Nelson Mandela Bay Ward 27' -> '27' (the dashboard adds the word 'Ward' itself)."""
    if not ward:
        return None
    match = re.search(r"(\d+)\s*$", str(ward))
    return match.group(1) if match else str(ward)


@app.get("/")
def home():
    return {"status": "ok", "message": "Report EC backend is running"}


@app.get("/reports")
def list_reports():
    reports = (
        supabase.table("reports")
        .select(DASHBOARD_COLUMNS)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    history = (
        supabase.table("report_updates")
        .select("report_id, text, created_at")
        .order("created_at")
        .execute()
        .data
    )

    updates_by_report = {}
    for row in history:
        updates_by_report.setdefault(row["report_id"], []).append(
            {"at": row["created_at"], "text": row["text"]}
        )

    for report in reports:
        report["ward"] = ward_number(report.get("ward"))
        received = {"at": report["created_at"], "text": "Report received on WhatsApp."}
        report["updates"] = [received] + updates_by_report.get(report["id"], [])
    return reports


class ReportUpdate(BaseModel):
    status: Literal["received", "awaiting_dispatch", "team_assigned", "in_progress", "resolved"]
    team: Optional[str] = Field(default=None, max_length=80)
    message: str = Field(min_length=1, max_length=1000)


@app.patch("/reports/{ref}")
def update_report(ref: str, update: ReportUpdate):
    found = supabase.table("reports").select("id, duplicate_of").eq("ref", ref).execute().data
    if not found:
        raise HTTPException(status_code=404, detail="Report not found")

    # Always work on the main report; its linked reports are updated with it
    master_id = found[0]["duplicate_of"] or found[0]["id"]
    group = (
        supabase.table("reports")
        .select("id, ref, phone")
        .or_(f"id.eq.{master_id},duplicate_of.eq.{master_id}")
        .execute()
        .data
    )
    ids = [r["id"] for r in group]

    changes = {"status": update.status}
    if update.team:
        changes["team"] = update.team
    supabase.table("reports").update(changes).in_("id", ids).execute()

    # First response = the first time anyone on the municipal side touched it
    now = datetime.now(timezone.utc).isoformat()
    supabase.table("reports").update({"first_response_at": now}).in_("id", ids).is_(
        "first_response_at", "null"
    ).execute()

    team_text = update.team or "No team assigned"
    supabase.table("report_updates").insert(
        {
            "report_id": master_id,
            "text": f"{STATUS_LABELS[update.status]} · {team_text}. Message sent: “{update.message}”",
        }
    ).execute()

    # One message per resident (a phone number that reported twice gets one message)
    sent = queued = 0
    seen = set()
    for row in group:
        phone = row.get("phone")
        if not phone or phone in seen:
            continue
        seen.add(phone)
        outbox = (
            supabase.table("notifications")
            .insert({"report_ref": row["ref"], "phone": phone, "body": update.message})
            .execute()
            .data[0]
        )
        try:
            send_whatsapp(phone, update.message)
            supabase.table("notifications").update({"sent_at": now}).eq("id", outbox["id"]).execute()
            sent += 1
        except Exception as error:
            supabase.table("notifications").update({"error": str(error)[:500]}).eq("id", outbox["id"]).execute()
            queued += 1

    return {"ok": True, "sent": sent, "queued": queued}


# Serves the dashboard at http://127.0.0.1:8000/dashboard/
# Keep this line at the bottom so it doesn't hide the API routes above.
app.mount("/dashboard", StaticFiles(directory="static", html=True), name="dashboard")