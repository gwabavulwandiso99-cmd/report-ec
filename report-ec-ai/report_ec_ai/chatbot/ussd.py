"""Adapts the chatbot to USSD (feature phones).

Most USSD aggregators (for example Africa's Talking) call your server with:
    sessionId, phoneNumber, serviceCode, text
where `text` holds ALL inputs of this dial joined by '*', e.g. "2*1*Zwide".
The reply must start with "CON " (keep the session open) or "END " (close it),
and one screen holds about 182 characters.

This module is NOT connected to a real USSD provider. Member 5 calls
`UssdGateway.handle(...)` from the provider's callback endpoint.
"""
import logging

from report_ec_ai.chatbot.conversation import ConversationEngine
from report_ec_ai.config import SETTINGS

log = logging.getLogger("report_ec_ai.ussd")


class UssdGateway:
    def __init__(self, engine: ConversationEngine, max_chars: int = SETTINGS.ussd_max_chars):
        self.engine = engine
        self.max_chars = max_chars
        self.shortened = 0   # how many screens were too long (tests check this stays 0)

    def handle(self, session_id: str, phone_number: str, text: str) -> str:
        text = text or ""
        if text == "":
            reply = self.engine.start_ussd_session(phone_number)
        else:
            latest = text.split("*")[-1]
            reply = self.engine.handle({
                "channel": "ussd",
                "user_id": phone_number,
                "text": latest,
                # Each step of a dial is unique; a repeated callback for the same step is ignored
                "message_id": f"{session_id}:{len(text.split('*'))}",
            })
            if reply.duplicate_message:
                return "CON " + self._fit("…")
        body = "\n".join(reply.messages)
        prefix = "END " if reply.end_session else "CON "
        return prefix + self._fit(body)

    def _fit(self, body: str) -> str:
        limit = self.max_chars - 4   # room for "CON " / "END "
        if len(body) <= limit:
            return body
        self.shortened += 1
        log.warning("USSD screen too long (%d chars), shortened", len(body))
        return body[:limit - 1] + "…"
