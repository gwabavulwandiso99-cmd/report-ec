"""Test helpers. All phone numbers here are made up (+27 82 000 ....)."""
from datetime import datetime, timedelta, timezone


class Clock:
    """A clock the tests can move forward."""

    def __init__(self):
        self.value = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class Chat:
    """Sends messages as one resident and keeps the last reply."""

    def __init__(self, engine, user_id="27820000001", channel="whatsapp"):
        self.engine = engine
        self.user_id = user_id
        self.channel = channel
        self.last = None

    def send(self, text=None, **extra):
        message = {"channel": self.channel, "user_id": self.user_id, "text": text, **extra}
        self.last = self.engine.handle(message)
        return self.last

    @property
    def text(self):
        return "\n".join(self.last.messages)

    @property
    def state(self):
        return self.last.state


def start_report(chat, language="1"):
    """Greeting -> language -> 'Report a problem'."""
    chat.send("hi")
    chat.send(language)
    chat.send("1")
    assert chat.state == "AWAITING_DESCRIPTION"
