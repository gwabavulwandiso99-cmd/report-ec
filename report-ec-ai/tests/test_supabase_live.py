"""Tests the real Supabase adapter against a LOCAL Postgres + PostgREST copy of the
Report EC tables (with database/001_ai_chatbot.sql applied).

Skipped unless REPORT_EC_TEST_POSTGREST_URL is set, e.g. http://127.0.0.1:3001
NEVER point this at the team's real Supabase project: it inserts test reports.
"""
import os
import uuid

import pytest

URL = os.getenv("REPORT_EC_TEST_POSTGREST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs a local PostgREST test database")

from helpers import Chat  # noqa: E402

from report_ec_ai.chatbot.conversation import ConversationEngine  # noqa: E402
from report_ec_ai.integration.schemas import StatusQuery  # noqa: E402


@pytest.fixture
def db():
    postgrest = pytest.importorskip("postgrest")
    return postgrest.SyncPostgrestClient(URL)


@pytest.fixture
def live(db):
    from report_ec_ai.integration.supabase_backend import SupabaseBackend, SupabaseSessionStore
    backend = SupabaseBackend(db)
    engine = ConversationEngine(backend, SupabaseSessionStore(db))
    return backend, engine


def new_phone():
    return "+2782" + str(uuid.uuid4().int)[:7]


def report_conversation(engine, phone, description="There is a burst water pipe near the clinic.", area="Zwide"):
    chat = Chat(engine, "whatsapp:" + phone)
    for text in ("hi", "1", "1", description, area, "0", "0"):
        chat.send(text)
    return chat, chat.send("1")


def test_report_is_saved_in_existing_and_new_columns(live, db):
    backend, engine = live
    phone = new_phone()
    chat, reply = report_conversation(engine, phone)
    assert reply.submitted_reference.startswith("EC-")
    row = db.table("reports").select("*").eq("ref", reply.submitted_reference).execute().data[0]
    assert row["phone"] == phone                      # no "whatsapp:" prefix, as before
    assert row["category"] == "water_leak"
    assert row["urgency"] == "high"
    assert row["status"] == "received"
    assert row["channel"] == "whatsapp"
    assert row["suburb"] == "Zwide"
    assert row["landmark"] == "Near the clinic"
    assert row["description"] == "There is a burst water pipe near the clinic."
    assert row["ai_suggestion"]["advisory_only"] is True
    assert row["ai_suggestion"]["category"] == "water"
    assert row["idempotency_key"]
    assert row["duplicate_of"] is None                # AI never links duplicates itself


def test_same_idempotency_key_returns_same_reference(live, db):
    backend, engine = live
    chat, reply = report_conversation(engine, new_phone(), "Pothole on the main road", "Motherwell")
    row = db.table("reports").select("*").eq("ref", reply.submitted_reference).execute().data[0]
    # Send a payload with the same key again (as a retry would)
    from report_ec_ai.integration.schemas import ReportPayload, AISuggestion, ReportLocation
    again = ReportPayload(
        idempotency_key=row["idempotency_key"], channel="whatsapp", language="en",
        description_original="Pothole on the main road", location=ReportLocation(area="Motherwell"),
        ai_suggestion=AISuggestion.model_validate(row["ai_suggestion"]), created_at=row["created_at"])
    result = backend.submit_report(again, "whatsapp:+27820000000")
    assert result.success and result.reference == reply.submitted_reference
    count = db.table("reports").select("id").eq("idempotency_key", row["idempotency_key"]).execute().data
    assert len(count) == 1


def test_possible_duplicates_are_suggested_not_linked(live, db):
    backend, engine = live
    report_conversation(engine, new_phone(), "Water leaking outside the clinic", "KwaDwesi")
    _, reply = report_conversation(engine, new_phone(), "Broken water pipe at the clinic entrance", "KwaDwesi")
    row = db.table("reports").select("ai_suggestion, duplicate_of").eq("ref", reply.submitted_reference).execute().data[0]
    assert row["ai_suggestion"]["possible_duplicates"]
    assert row["duplicate_of"] is None


def test_status_only_for_the_reporter(live):
    backend, engine = live
    phone = new_phone()
    _, reply = report_conversation(engine, phone)
    ref = reply.submitted_reference
    mine = backend.get_report_status(StatusQuery(reference=ref, requester_id="whatsapp:" + phone, channel="whatsapp"))
    assert mine.found and mine.status == "received"
    local_format = "0" + phone[3:]                     # +2782... written as 082...
    assert backend.get_report_status(StatusQuery(reference=ref, requester_id=local_format, channel="ussd")).found
    other = backend.get_report_status(StatusQuery(reference=ref, requester_id=new_phone(), channel="whatsapp"))
    assert other.found is False


def test_verification_flow_on_a_resolved_report(live, db):
    backend, engine = live
    phone = new_phone()
    report = db.table("reports").insert({"phone": phone, "category": "water_leak", "status": "resolved",
                                         "description": "Burst pipe"}).execute().data[0]
    db.table("report_updates").insert({"report_id": report["id"], "text":
        "Resolved · Water & sanitation · by Test Staff. Message sent: “The burst pipe has been repaired.”"}).execute()

    chat = Chat(engine, "whatsapp:" + phone)
    for text in ("hi", "1", "3", report["ref"]):
        chat.send(text)
    assert "The burst pipe has been repaired." in chat.text
    assert "Test Staff" not in chat.text              # staff names are not shown to residents
    chat.send("2")
    chat.send("Still leaking, I have a new photo")
    assert "has been recorded" in chat.text

    saved = db.table("report_verifications").select("*").eq("report_id", report["id"]).execute().data
    assert len(saved) == 1
    assert saved[0]["stance"] == "dispute"
    assert saved[0]["ai_advisory"]["contradiction_detected"] is True
    status = db.table("reports").select("status").eq("id", report["id"]).execute().data[0]["status"]
    assert status == "resolved"                         # not changed by the AI
    summary = db.table("report_verification_summary").select("*").eq("report_id", report["id"]).execute().data
    assert summary[0]["disputes"] == 1 and summary[0]["contradiction_flagged"] is True


def test_sessions_are_stored_with_hashed_keys(live, db):
    backend, engine = live
    phone = new_phone()
    chat = Chat(engine, "whatsapp:" + phone)
    chat.send("hi")
    chat.send("1")
    rows = db.table("bot_sessions").select("phone, step, data").like("phone", "whatsapp:%").execute().data
    assert any(r["step"] == "MAIN_MENU" for r in rows)
    assert all(phone not in r["phone"] and phone not in str(r["data"]) for r in rows)
    # A new engine (server restart) continues the same conversation from the database
    from report_ec_ai.integration.supabase_backend import SupabaseSessionStore
    engine2 = ConversationEngine(backend, SupabaseSessionStore(db))
    assert Chat(engine2, "whatsapp:" + phone).send("1").state == "AWAITING_DESCRIPTION"


def test_existing_rows_are_left_alone(db):
    rows = db.table("bot_sessions").select("phone").eq("phone", "whatsapp:+27820000123").execute().data
    assert rows, "a pre-existing bot_sessions row must not be touched"
