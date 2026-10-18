import os
from datetime import datetime, timezone
import re
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from auth import LoginError, verify_credentials
from db import supabase
from notify import send_whatsapp

# AI chatbot (Member 6). Its code is in the report-ec-ai folder.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "report-ec-ai"))
from report_ec_ai.integration.supabase_backend import create_live_service  # noqa: E402
from report_ec_ai.integration.twilio_whatsapp import create_whatsapp_router  # noqa: E402

load_dotenv()

SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is missing from .env")

# Set COOKIE_SECURE=1 in .env once the site runs on https (for example on Render)
COOKIE_SECURE = os.getenv("COOKIE_SECURE") == "1"

app = FastAPI(title="Report EC")

# ---------------------------------------------------------------------------
# Login. The dashboard shows exact locations, photos and resident messages, and
# PATCH sends WhatsApp messages, so none of it may be open to the internet.
# Staff sign in on /login with their own account (see add_staff.py).
# The WhatsApp webhook must NOT be added here, or Meta can't deliver messages.
# ---------------------------------------------------------------------------
STAFF_ONLY_PATHS = ("/dashboard", "/reports", "/me")


@app.middleware("http")
async def require_staff_login(request: Request, call_next):
    path = request.url.path
    if path.startswith(STAFF_ONLY_PATHS) and not request.session.get("staff"):
        if path.startswith("/dashboard"):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": "Please log in."}, status_code=401)
    return await call_next(request)


# Added after the login check so it runs first and the session is ready for it
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=8 * 60 * 60,  # staff stay logged in for one working day
    same_site="lax",
    https_only=COOKIE_SECURE,
)


class LoginDetails(BaseModel):
    email: str
    password: str


@app.get("/login")
def login_page(request: Request):
    if request.session.get("staff"):
        return RedirectResponse("/dashboard/", status_code=303)
    return FileResponse("static/login.html")


@app.post("/login")
def login(details: LoginDetails, request: Request):
    email = details.email.strip().lower()
    if not email or not details.password:
        raise HTTPException(status_code=400, detail="Please enter your email and password.")

    try:
        staff = verify_credentials(email, details.password)
    except LoginError as error:
        raise HTTPException(status_code=401, detail=str(error))

    request.session.clear()
    request.session["staff"] = staff
    return {"ok": True, "name": staff["name"]}


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/me")
def current_staff(request: Request):
    return request.session["staff"]


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
def update_report(ref: str, update: ReportUpdate, request: Request):
    staff = request.session["staff"]

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

    # The staff member's name goes into the history, so every update can be traced
    team_text = update.team or "No team assigned"
    supabase.table("report_updates").insert(
        {
            "report_id": master_id,
            "text": f"{STATUS_LABELS[update.status]} · {team_text} · by {staff['name']}. "
                    f"Message sent: “{update.message}”",
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

# WhatsApp chatbot. Twilio sends every resident message to /webhook/whatsapp.
# Not behind the staff login (Twilio can't log in); every call is checked
# with Twilio's signature instead (TWILIO_AUTH_TOKEN + PUBLIC_BASE_URL in .env).
ai = create_live_service(supabase)
app.include_router(create_whatsapp_router(ai))
# Serves the dashboard at http://127.0.0.1:8000/dashboard/
# Keep this line at the bottom so it doesn't hide the routes above.
app.mount("/dashboard", StaticFiles(directory="static", html=True), name="dashboard")
