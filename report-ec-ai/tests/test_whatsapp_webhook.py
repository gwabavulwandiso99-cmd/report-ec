"""Twilio WhatsApp webhook: signature check, form mapping, TwiML replies."""
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from report_ec_ai.integration.service import ReportEcAIService
from report_ec_ai.integration.twilio_whatsapp import (
    create_whatsapp_router,
    to_incoming_message,
    twilio_signature,
    twiml,
)

TOKEN = "test-auth-token"
BASE = "https://demo.ngrok-free.app"


def make_client(monkeypatch, token=TOKEN, base=BASE):
    monkeypatch.delenv("WHATSAPP_SKIP_SIGNATURE", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    service = ReportEcAIService()   # mock backend
    app = FastAPI()
    app.include_router(create_whatsapp_router(service, auth_token=token, public_base_url=base))
    return TestClient(app), service


def post(client, form, sign=True, url=BASE + "/webhook/whatsapp"):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if sign:
        headers["X-Twilio-Signature"] = twilio_signature(TOKEN, url, form)
    return client.post("/webhook/whatsapp", content=urlencode(form), headers=headers)


def form(body, sid="SM1", **extra):
    return {"From": "whatsapp:+27820000001", "To": "whatsapp:+14155238886", "Body": body,
            "MessageSid": sid, "NumMedia": "0", **extra}


def test_signature_matches_twilios_own_library():
    # Expected values were computed with Twilio's official RequestValidator
    # (twilio 9.12.0, twilio.request_validator), so our code agrees with Twilio.
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234",
              "From": "+12349013030", "To": "+18005551212"}
    assert twilio_signature("12345", url, params) == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="
    xhosa = {"From": "whatsapp:+27820000001", "Body": "Kukho umbhobho wamanzi ogqabhukileyo kufuphi nekliniki.",
             "MessageSid": "SM1", "NumMedia": "0"}
    assert twilio_signature(TOKEN, BASE + "/webhook/whatsapp", xhosa) == "9RcI/mJiXmRktADBuqA8WIJCeAk="


def test_signature_is_required(monkeypatch):
    client, _ = make_client(monkeypatch)
    assert post(client, form("hi"), sign=False).status_code == 403
    assert post(client, form("hi"), url="https://wrong.example/webhook/whatsapp").status_code == 403
    ok = post(client, form("hi"))
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("application/xml")
    assert "Choose a language" in ok.text


def test_not_configured_fails_closed(monkeypatch):
    client, _ = make_client(monkeypatch, token=None, base=None)
    assert post(client, form("hi")).status_code == 503


def test_full_report_over_whatsapp(monkeypatch):
    client, service = make_client(monkeypatch)
    for i, text in enumerate(["Hi", "1", "1", "Burst pipe near the clinic in Zwide", "0", "0"]):
        post(client, form(text, sid=f"SM{i}"))
    reply = post(client, form("1", sid="SM99"))
    assert "MOCK-EC-1001" in reply.text
    assert service.backend.reports["MOCK-EC-1001"]["reporter_id"] == "whatsapp:+27820000001"
    again = post(client, form("1", sid="SM99"))              # Twilio retries the same message
    assert "<Message>" not in again.text
    assert service.backend.submit_calls == 1


def test_form_mapping():
    message = to_incoming_message(form("", NumMedia="1", MediaUrl0="https://api.twilio.com/m/1",
                                       MediaContentType0="image/jpeg", Latitude="-33.87", Longitude="25.56"))
    assert message["text"] is None
    assert message["media"] == [{"media_id": "https://api.twilio.com/m/1", "mime_type": "image/jpeg"}]
    assert message["location"] == {"latitude": -33.87, "longitude": 25.56}
    assert "location" not in to_incoming_message(form("x", Latitude="abc", Longitude="1"))


def test_twiml_escapes_and_splits():
    xml = twiml(["<script>&", "second"])
    assert "&lt;script&gt;&amp;" in xml and xml.count("<Message>") == 1
    assert twiml(["x" * 3500]).count("<Message>") == 3
    assert "<Message>" not in twiml([])


def test_chatbot_crash_gives_a_polite_message(monkeypatch):
    client, service = make_client(monkeypatch)

    def broken(_):
        raise RuntimeError("boom")

    service.process_message = broken
    reply = post(client, form("hi"))
    assert reply.status_code == 200 and "something went wrong" in reply.text
