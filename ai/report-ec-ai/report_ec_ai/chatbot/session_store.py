"""Conversation sessions and where they are stored.

Privacy: a session is stored under a one-way hash of (channel, user id). The
resident's phone number is not stored in the session. The chatbot receives it
with each message and passes it straight to the backend adapter when needed.

Two stores are included:
- InMemorySessionStore: for demos and tests (lost when Python stops)
- JsonFileSessionStore: one JSON file per session, survives restarts; shows
  how a persistent store plugs in. Member 5 can write a database-backed store
  with the same three methods (see docs/INTEGRATION.md).
"""
import hashlib
import hmac
import json
import os
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from report_ec_ai.chatbot.states import State
from report_ec_ai.integration.schemas import MediaItem

_DEV_SALT = "report-ec-local-development-salt"


def session_key(channel: str, user_id: str, salt: Optional[str] = None) -> str:
    """Stable, non-reversible key. Set REPORT_EC_SESSION_SALT in production."""
    salt = salt or os.getenv("REPORT_EC_SESSION_SALT") or _DEV_SALT
    digest = hmac.new(salt.encode(), f"{channel}:{user_id}".encode(), hashlib.sha256).hexdigest()
    return f"{channel}:{digest[:32]}"


def masked(key: str) -> str:
    """Short form for logs, e.g. 'whatsapp:3fa9…'."""
    channel, _, digest = key.partition(":")
    return f"{channel}:{digest[:4]}…"


class DraftLocation(BaseModel):
    area: Optional[str] = None
    municipality: Optional[str] = None
    municipality_source: str = "pilot_default"
    landmark: Optional[str] = None
    landmark_words: Optional[str] = None
    location_text: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    needs_review: bool = False

    @property
    def has_any(self) -> bool:
        return bool(self.area or self.landmark or self.location_text or self.latitude is not None)


class ReportDraft(BaseModel):
    description_original: Optional[str] = None
    description_source: str = "resident_text"
    analysis: Optional[dict] = None              # ComplaintAnalysis as a dict
    resident_category: Optional[str] = None
    resident_subcategory: Optional[str] = None
    location: DraftLocation = Field(default_factory=DraftLocation)
    ward: Optional[str] = None
    ward_asked: bool = False
    evidence: list[MediaItem] = Field(default_factory=list)
    evidence_asked: bool = False
    idempotency_key: Optional[str] = None
    short_description_warned: bool = False
    location_attempts: int = 0
    awaiting_area_only: bool = False
    ussd_typing_other: bool = False


class VerificationDraft(BaseModel):
    reference: str
    claim: Optional[str] = None
    stance: Optional[str] = None
    statement: Optional[str] = None
    evidence: list[MediaItem] = Field(default_factory=list)
    idempotency_key: Optional[str] = None


class Session(BaseModel):
    key: str
    channel: str
    language: Optional[str] = None
    state: State = State.LANGUAGE_SELECTION
    previous_states: list[State] = Field(default_factory=list)   # for "back"
    return_state: Optional[State] = None                          # where to go after changing language
    editing: bool = False                                         # came from the edit menu
    draft: Optional[ReportDraft] = None
    verification: Optional[VerificationDraft] = None
    submitted: dict = Field(default_factory=dict)                 # idempotency_key -> reference
    last_reference: Optional[str] = None
    invalid_count: int = 0
    seen_message_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionStore(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[Session]:
        ...

    @abstractmethod
    def save(self, session: Session) -> None:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...


class InMemorySessionStore(SessionStore):
    def __init__(self, ttl_hours: int = 24, now=None):
        self._data = {}
        self._lock = threading.Lock()
        self._ttl = timedelta(hours=ttl_hours)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def get(self, key):
        with self._lock:
            raw = self._data.get(key)
            if raw is None:
                return None
            session = Session.model_validate_json(raw)
            if self._now() - session.updated_at > self._ttl:
                del self._data[key]
                return None
            return session

    def save(self, session):
        # Stored as JSON text so callers can't change a stored session by accident
        with self._lock:
            self._data[session.key] = session.model_dump_json()

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)

    def __len__(self):
        return len(self._data)


class JsonFileSessionStore(SessionStore):
    def __init__(self, folder: str, ttl_hours: int = 24, now=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._ttl = timedelta(hours=ttl_hours)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        # The key is already a hash; replace ":" so it is a valid Windows file name
        return self.folder / (key.replace(":", "_") + ".json")

    def get(self, key):
        path = self._path(key)
        with self._lock:
            if not path.exists():
                return None
            try:
                session = Session.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValueError, json.JSONDecodeError):
                path.unlink(missing_ok=True)       # damaged file: start a fresh session
                return None
            if self._now() - session.updated_at > self._ttl:
                path.unlink(missing_ok=True)
                return None
            return session

    def save(self, session):
        path = self._path(session.key)
        temp = path.with_suffix(".tmp")
        with self._lock:
            temp.write_text(session.model_dump_json(), encoding="utf-8")
            os.replace(temp, path)

    def delete(self, key):
        with self._lock:
            self._path(key).unlink(missing_ok=True)
