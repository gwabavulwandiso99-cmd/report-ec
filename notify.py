"""Sends WhatsApp messages to residents.

If the Twilio variables are set in .env, messages are sent through Twilio's
WhatsApp API. If not, send_whatsapp() returns False and the message simply stays
in the `notifications` table (sent_at is empty) until it is sent some other way.

Note for Twilio: outside the 24-hour window after a resident last wrote to the
bot, WhatsApp only allows pre-approved template messages.
"""
import os

import httpx

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")  # e.g. whatsapp:+14155238886


def send_whatsapp(phone: str, body: str) -> None:
    """Raises an exception if the message could not be sent."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        raise RuntimeError("WhatsApp sending is not configured (Twilio variables missing)")

    to = phone if phone.startswith("whatsapp:") else f"whatsapp:{phone}"
    response = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={"From": TWILIO_WHATSAPP_FROM, "To": to, "Body": body},
        timeout=15,
    )
    response.raise_for_status()