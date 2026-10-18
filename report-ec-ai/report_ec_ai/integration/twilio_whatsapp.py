"""WhatsApp webhook for Twilio (the provider notify.py already uses).

Twilio calls this URL every time a resident writes to the WhatsApp number. The
chatbot answers in the same HTTP response (TwiML), so no extra API call is needed.

In main.py (before the app.mount line):

    from report_ec_ai.integration.twilio_whatsapp import create_whatsapp_router
    app.include_router(create_whatsapp_router(ai))

Settings (.env):
    TWILIO_AUTH_TOKEN   used to check that requests really come from Twilio
    PUBLIC_BASE_URL     the public https address Twilio calls, e.g. https://abc123.ngrok-free.app
                        (needed for the check because the app itself only sees 127.0.0.1)
    WHATSAPP_SKIP_SIGNATURE=1   LOCAL TESTING ONLY: turns the check off
"""
import base64
import hashlib
import hmac
import logging
import os
from typing import Optional
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

log = logging.getLogger("report_ec_ai.whatsapp")

TWILIO_MAX_BODY = 1600   # characters per WhatsApp message through Twilio


def twilio_signature(auth_token: str, url: str, params: dict) -> str:
    """Twilio's documented scheme: HMAC-SHA1 over the URL plus every POST
    parameter (sorted by name, name followed by value), base64-encoded."""
    data = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def to_incoming_message(form: dict) -> dict:
    """Twilio form fields -> the chatbot's IncomingMessage."""
    message = {
        "channel": "whatsapp",
        "user_id": form["From"],
        "message_id": form.get("MessageSid") or None,
        "text": form.get("Body") or None,
    }
    media = []
    for i in range(min(int(form.get("NumMedia", "0") or 0), 3)):
        url, kind = form.get(f"MediaUrl{i}"), form.get(f"MediaContentType{i}")
        if url and kind:
            media.append({"media_id": url, "mime_type": kind})
    if media:
        message["media"] = media
    if form.get("Latitude") and form.get("Longitude"):
        try:
            message["location"] = {"latitude": float(form["Latitude"]), "longitude": float(form["Longitude"])}
        except ValueError:
            pass
    return message


def twiml(messages: list) -> str:
    """One WhatsApp message (keeps the order), split only if it is too long."""
    text = "\n\n".join(messages)
    parts = [text[i:i + TWILIO_MAX_BODY] for i in range(0, len(text), TWILIO_MAX_BODY)] if text else []
    body = "".join(f"<Message>{escape(part)}</Message>" for part in parts)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def create_whatsapp_router(service, auth_token: Optional[str] = None,
                           public_base_url: Optional[str] = None, path: str = "/webhook/whatsapp") -> APIRouter:
    router = APIRouter(tags=["WhatsApp"])

    @router.post(path)
    async def whatsapp_webhook(request: Request):
        token = auth_token or os.getenv("TWILIO_AUTH_TOKEN")
        base = (public_base_url or os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        skip_check = os.getenv("WHATSAPP_SKIP_SIGNATURE") == "1"

        raw = (await request.body()).decode("utf-8", errors="replace")
        form = dict(parse_qsl(raw, keep_blank_values=True))

        if not skip_check:
            if not token or not base:
                log.error("TWILIO_AUTH_TOKEN or PUBLIC_BASE_URL is not set; refusing webhook calls")
                raise HTTPException(status_code=503, detail="WhatsApp webhook is not configured")
            url = base + request.url.path + (f"?{request.url.query}" if request.url.query else "")
            expected = twilio_signature(token, url, form)
            if not hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", "")):
                raise HTTPException(status_code=403, detail="Invalid Twilio signature")

        if not form.get("From"):
            return Response(twiml([]), media_type="application/xml")
        try:
            reply = service.process_message(to_incoming_message(form))
        except Exception as error:   # never show a crash to the resident, never pretend it worked
            log.exception("chatbot error: %s", type(error).__name__)
            return Response(twiml(["Sorry, something went wrong on our side. Please try again in a few minutes. "
                                   "/ Uxolo, kukho ingxaki kuthi. Nceda uzame kwakhona emva kwemizuzu embalwa."]),
                            media_type="application/xml")
        return Response(twiml(reply.messages), media_type="application/xml")

    return router
