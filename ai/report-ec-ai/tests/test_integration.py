"""Backend contract, status lookup, USSD, FastAPI router and file session store
(required test 25 and integration checks)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from helpers import Chat, start_report
from report_ec_ai.chatbot.conversation import ConversationEngine
from report_ec_ai.chatbot.session_store import JsonFileSessionStore, Session, session_key
from report_ec_ai.chatbot.ussd import UssdGateway
from report_ec_ai.integration.backend_adapter import CallbackBackendAdapter, MockBackendAdapter
from report_ec_ai.integration.router import create_router
from report_ec_ai.integration.schemas import StatusResult, SubmissionResult, VerificationSubmitResult
from report_ec_ai.integration.service import ReportEcAIService

OWNER = "27820000001"
STRANGER = "27820000002"


# 25. Status lookup authorization contract
def test_status_lookup_only_for_the_reporter(engine, backend):
    owner = Chat(engine, OWNER)
    start_report(owner)
    owner.send("Burst pipe near the clinic in Zwide")
    owner.send("0"); owner.send("0")
    reference = owner.send("1").submitted_reference

    owner.send("menu")
    owner.send("2")
    assert "reply 1 to check that one" in owner.text
    owner.send("1")                                   # shortcut to the last report
    assert reference in owner.text and "Submitted" in owner.text

    stranger = Chat(engine, STRANGER)
    stranger.send("hi"); stranger.send("1"); stranger.send("2")
    stranger.send(reference)
    assert "couldn't find a report" in stranger.text
    assert reference not in stranger.text.replace("EC-1042", "")
    stranger.send("EC-999999")                         # does not exist: same answer
    assert "couldn't find a report" in stranger.text


def test_status_query_sends_requester_id_to_backend():
    seen = []

    def status(query):
        seen.append(query)
        return {"found": True, "authorized": True, "reference": query.reference, "status": "team_assigned",
                "updated_at": datetime(2026, 10, 15, 10, tzinfo=timezone.utc)}

    backend = CallbackBackendAdapter(
        submit_report=lambda p, r: {"success": False},
        get_report_status=status,
        submit_verification=lambda p, r: {"success": False},
    )
    chat = Chat(ConversationEngine(backend), OWNER)
    chat.send("hi"); chat.send("1"); chat.send("2"); chat.send("EC-1042")
    assert seen[0].requester_id == OWNER
    assert seen[0].purpose == "status"
    assert "Acknowledged" in chat.text          # legacy team_assigned explained in plain words
    assert "15 Oct 2026 12:00" in chat.text     # shown in South African time


def test_status_backend_down(engine, backend):
    backend.offline = True
    chat = Chat(engine)
    chat.send("hi"); chat.send("1"); chat.send("2"); chat.send("EC-1042")
    assert "can't check the status right now" in chat.text
    assert chat.state == "MAIN_MENU"


def test_invalid_reference_format(engine):
    chat = Chat(engine)
    chat.send("hi"); chat.send("1"); chat.send("2"); chat.send("my report from monday")
    assert "doesn't look like a reference" in chat.text


def test_callback_adapter_validates_backend_answers():
    adapter = CallbackBackendAdapter(lambda p, r: {"success": True, "reference": "EC-5"},
                                     lambda q: {"found": False},
                                     lambda p, r: {"success": True})
    assert isinstance(adapter.get_report_status(None), StatusResult)
    with pytest.raises(Exception):
        CallbackBackendAdapter(lambda p, r: {"success": "maybe?"}, None, None).submit_report(None, "x")


# --- USSD -------------------------------------------------------------------
def test_ussd_report_flow_and_screen_length():
    backend = MockBackendAdapter()
    gateway = UssdGateway(ConversationEngine(backend))
    screens = []

    def dial(text):
        screen = gateway.handle("ATsession1", "+27820000005", text)
        screens.append(screen)
        return screen

    assert dial("").startswith("CON ")
    dial("2")                    # isiXhosa
    dial("2*1")                  # report a problem
    screen = dial("2*1*4")       # hanging wires
    assert "112" in screen       # short safety line
    dial("2*1*4*Zwide kufuphi nesikolo")
    screen = dial("2*1*4*Zwide kufuphi nesikolo*0")   # ward unknown -> summary (no photo step on USSD)
    assert "Thumela" in screen
    screen = dial("2*1*4*Zwide kufuphi nesikolo*0*1")
    assert screen.startswith("END ")
    assert "MOCK-EC-1001" in screen
    for s in screens:
        assert len(s) <= 182, s
    assert gateway.shortened == 0
    payload = backend.reports["MOCK-EC-1001"]["payload"]
    assert payload.channel == "ussd"
    assert payload.description_source == "ussd_menu"
    assert payload.ai_suggestion.category == "electricity"
    assert payload.ai_suggestion.priority == "critical"


@pytest.mark.parametrize("language", ["1", "2"])
def test_every_ussd_screen_fits(language):
    """Walk through every main USSD path in both languages and check the screen size."""
    backend = MockBackendAdapter()
    backend.add_report("EC-1042", "+27820000006", "pending_verification", "Pipe repaired")
    gateway = UssdGateway(ConversationEngine(backend))
    paths = [
        ["1", "9", "The swings at the park in Zwide are broken and children can get hurt badly", "45", "2", "1", "Zwide", "1"],
        ["2", "EC-1042"],
        ["3", "EC-1042", "2", "Still leaking"],
        ["4"],
        ["1", "1", "x", "Motherwell", "abc", "0", "3"],
        ["1", "4", "Zwide near school", "0", "2", "4", "8", "1"],
        ["1", "6", "KwaZakhele", "12", "2", "1", "7", "1"],
        ["1", "2", "Zwide", "0", "1"],
        ["5", "2"],
        ["1", "9", "?", "Hello there friend", "8", "Zwide", "0", "1"],
    ]
    for number, path in enumerate(paths):
        session = f"S{language}{number}"
        text = ""
        screen = gateway.handle(session, "+27820000006", text)
        assert len(screen) <= 182
        text = language
        screen = gateway.handle(session, "+27820000006", text)
        for step in path:
            text = f"{text}*{step}"
            screen = gateway.handle(session, "+27820000006", text)
            assert len(screen) <= 182, screen
            assert screen[:4] in ("CON ", "END ")
    assert gateway.shortened == 0, "a USSD screen had to be cut short"


# --- FastAPI router ------------------------------------------------------------
def make_client(key="test-key-123"):
    service = ReportEcAIService(backend=MockBackendAdapter())
    app = FastAPI()
    app.include_router(create_router(service, internal_key=key))
    return TestClient(app)


def test_router_requires_internal_key():
    client = make_client()
    assert client.post("/ai/complaints/analyze", json={"text": "Burst pipe"}).status_code == 401
    assert client.post("/ai/complaints/analyze", json={"text": "Burst pipe"},
                       headers={"X-Internal-Key": "wrong"}).status_code == 401


def test_router_fails_closed_without_configured_key(monkeypatch):
    monkeypatch.delenv("REPORT_EC_AI_INTERNAL_KEY", raising=False)
    service = ReportEcAIService()
    app = FastAPI()
    app.include_router(create_router(service))
    client = TestClient(app)
    assert client.get("/ai/health", headers={"X-Internal-Key": "anything"}).status_code == 503


def test_router_endpoints():
    client = make_client()
    h = {"X-Internal-Key": "test-key-123"}
    assert client.get("/ai/health", headers=h).json() == {"ok": True, "mock_backend": True}

    analysis = client.post("/ai/complaints/analyze", json={"text": "Exposed cable near the school"}, headers=h).json()
    assert analysis["priority"]["priority"] == "critical"
    assert analysis["advisory_only"] is True

    reply = client.post("/ai/chat/message", json={"channel": "whatsapp", "user_id": OWNER, "text": "hi"}, headers=h).json()
    assert reply["state"] == "LANGUAGE_SELECTION"

    now = "2026-10-15T09:00:00+00:00"
    dup = client.post("/ai/complaints/duplicates", headers=h, json={
        "new_report": {"reference": "N", "description": "Burst pipe near the clinic", "area": "Zwide", "created_at": now},
        "existing_reports": [{"reference": "O", "description": "Water leaking outside the clinic", "area": "Zwide", "created_at": now}],
    }).json()
    assert dup["candidates"][0]["reference"] == "O"

    ver = client.post("/ai/verification/analyze", headers=h, json={
        "municipal_claim": "Fixed", "statements": [{"text": "still leaking"}]}).json()
    assert ver["contradiction_detected"] is True and ver["changes_status"] is False

    trends = client.post("/ai/trends/summary", headers=h, json={
        "data_label": "test", "as_of": now,
        "reports": [{"reference": "A", "category": "water", "area": "Zwide", "status": "submitted",
                     "created_at": "2026-10-14T09:00:00+00:00", "phone": "27820000001"}]}).json()
    assert trends["total_reports"] == 1 and "27820000001" not in str(trends)

    ussd = client.post("/ai/chat/ussd", headers=h, json={"session_id": "s", "phone_number": "+2782", "text": ""})
    assert ussd.text.startswith("CON ")


def test_router_rejects_malformed_input():
    client = make_client()
    h = {"X-Internal-Key": "test-key-123"}
    assert client.post("/ai/chat/message", json={"channel": "whatsapp"}, headers=h).status_code == 422
    assert client.post("/ai/complaints/analyze", json={"text": "x" * 5000}, headers=h).status_code == 422
    assert client.post("/ai/complaints/analyze", json={"text": "x", "forced_category": "aliens"},
                       headers=h).status_code == 422
    assert client.post("/ai/trends/summary", json={"reports": "nope", "data_label": "x"}, headers=h).status_code == 422


# --- Persistent session store ---------------------------------------------------
def test_json_file_store_survives_a_restart(tmp_path):
    backend = MockBackendAdapter()
    first = Chat(ConversationEngine(backend, JsonFileSessionStore(tmp_path)), OWNER)
    start_report(first)
    first.send("Burst pipe near the clinic in Zwide")
    assert first.state == "AWAITING_WARD"

    # A new engine (as if the server restarted) continues the same conversation
    second = Chat(ConversationEngine(backend, JsonFileSessionStore(tmp_path)), OWNER)
    second.send("0")
    assert second.state == "AWAITING_EVIDENCE"
    for file in tmp_path.iterdir():
        assert OWNER not in file.read_text(encoding="utf-8")
        assert OWNER not in file.name


def test_json_file_store_handles_damaged_file(tmp_path):
    store = JsonFileSessionStore(tmp_path)
    key = session_key("whatsapp", OWNER)
    (tmp_path / (key.replace(":", "_") + ".json")).write_text("{not json", encoding="utf-8")
    assert store.get(key) is None


def test_json_file_store_expiry(tmp_path):
    now = [datetime(2026, 10, 16, tzinfo=timezone.utc)]
    store = JsonFileSessionStore(tmp_path, ttl_hours=24, now=lambda: now[0])
    store.save(Session(key="whatsapp:abc", channel="whatsapp", updated_at=now[0]))
    assert store.get("whatsapp:abc") is not None
    now[0] += timedelta(hours=25)
    assert store.get("whatsapp:abc") is None


def test_service_facade():
    ai = ReportEcAIService()
    assert ai.uses_mock_backend
    assert ai.analyze_complaint("Burst pipe").category.category == "water"
    assert ai.analyze_complaint({"text": "Akukho mbane"}).category.category == "electricity"
    assert ai.process_message({"channel": "test", "user_id": "u1", "text": "hi"}).state == "LANGUAGE_SELECTION"


def test_backend_result_types():
    assert SubmissionResult(success=False).reference is None
    assert VerificationSubmitResult(success=True).success
