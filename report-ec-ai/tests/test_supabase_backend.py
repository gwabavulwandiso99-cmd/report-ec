"""Supabase adapter tests that need no database: a small fake client stands in for
supabase-py. (tests/test_supabase_live.py runs the same adapter on real Postgres.)"""
from datetime import datetime, timezone

from helpers import Chat

from report_ec_ai.chatbot.conversation import ConversationEngine
from report_ec_ai.integration.schemas import StatusQuery
from report_ec_ai.integration.supabase_backend import (
    SupabaseBackend,
    SupabaseSessionStore,
    phone_digits,
    resident_message,
)


class FakeError(Exception):
    def __init__(self, code):
        super().__init__(f"error {code}")
        self.code = code


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.action, self.payload = db, table, [], "select", None
        self._limit = None

    # builder methods used by the adapter
    def select(self, *_):
        return self

    def eq(self, column, value):
        self.filters.append(lambda r: r.get(column) == value)
        return self

    def or_(self, text):
        parts = [p.split(".eq.") for p in text.split(",")]
        self.filters.append(lambda r: any(str(r.get(c)) == v for c, v in parts))
        return self

    def gte(self, column, value):
        self.filters.append(lambda r: str(r.get(column)) >= value)
        return self

    def lt(self, column, value):
        self.filters.append(lambda r: str(r.get(column)) < value)
        return self

    def filter(self, *_):
        return self

    def order(self, *_, **__):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, row):
        self.action, self.payload = "insert", row
        return self

    def upsert(self, row, on_conflict=""):
        self.action, self.payload = "upsert", row
        return self

    def delete(self):
        self.action = "delete"
        return self

    def execute(self):
        return type("Response", (), {"data": self.db.run(self)})()


class FakeDB:
    def __init__(self):
        self.tables = {"reports": [], "report_updates": [], "report_verifications": [], "bot_sessions": []}
        self.fail_inserts = None    # e.g. FakeError("23505") or RuntimeError()

    def table(self, name):
        return Query(self, name)

    def run(self, q):
        rows = self.tables[q.table]
        if q.action == "insert":
            if self.fail_inserts:
                error, self.fail_inserts = self.fail_inserts, None
                raise error
            key = q.payload.get("idempotency_key")
            if key and any(r.get("idempotency_key") == key for r in rows):
                raise FakeError("23505")
            new = {"id": len(rows) + 1, **q.payload}
            if q.table == "reports":
                new.setdefault("status", "received")
                new.update(ref=f"EC-{len(rows) + 1:04d}", duplicate_of=new.get("duplicate_of"),
                           created_at=datetime.now(timezone.utc).isoformat(),
                           updated_at=datetime.now(timezone.utc).isoformat())
            rows.append(new)
            return [new]
        if q.action == "upsert":
            rows[:] = [r for r in rows if r["phone"] != q.payload["phone"]] + [q.payload]
            return [q.payload]
        matched = [r for r in rows if all(f(r) for f in q.filters)]
        if q.action == "delete":
            for r in matched:
                rows.remove(r)
            return matched
        return matched[: q._limit] if q._limit else matched


def make():
    db = FakeDB()
    backend = SupabaseBackend(db)
    return db, backend, ConversationEngine(backend, SupabaseSessionStore(db))


def submit(engine, user="whatsapp:+27820000001"):
    chat = Chat(engine, user)
    for text in ("hi", "1", "1", "Burst pipe near the clinic in Zwide", "0", "0"):
        chat.send(text)
    return chat, chat.send("1")


def test_report_row_matches_existing_constraints():
    db, backend, engine = make()
    _, reply = submit(engine)
    row = db.tables["reports"][0]
    assert reply.submitted_reference == row["ref"] == "EC-0001"
    assert row["phone"] == "+27820000001"
    assert row["category"] in {"water_leak", "no_water", "power_outage", "pothole", "streetlight", "other"}
    assert row["urgency"] in {"critical", "high", "medium"}
    assert row["status"] == "received"
    assert row["ai_suggestion"]["advisory_only"] is True


def test_low_urgency_is_stored_as_medium_for_the_current_check_constraint():
    db, backend, engine = make()
    chat = Chat(engine)
    for text in ("hi", "1", "1", "Streetlight broken in Zwide", "0", "0"):
        chat.send(text)
    chat.send("1")
    assert db.tables["reports"][-1]["urgency"] == "medium"
    assert db.tables["reports"][-1]["ai_suggestion"]["priority"] == "low"


def test_database_error_is_reported_as_not_sent():
    db, backend, engine = make()
    db.fail_inserts = RuntimeError("connection lost")
    _, reply = submit(engine)
    assert reply.submitted_reference is None
    assert reply.state == "SUBMISSION_FAILED"
    assert db.tables["reports"] == []


def test_unique_violation_race_returns_existing_reference():
    db, backend, engine = make()
    _, first = submit(engine, "whatsapp:+27820000002")
    key = db.tables["reports"][0]["idempotency_key"]
    from report_ec_ai.integration.schemas import ReportPayload
    payload = ReportPayload.model_validate({
        "idempotency_key": key, "channel": "whatsapp", "language": "en", "description_original": "x",
        "location": {}, "created_at": datetime.now(timezone.utc),
        "ai_suggestion": db.tables["reports"][0]["ai_suggestion"]})
    original_find = backend._find_by_key
    backend._find_by_key = lambda k, calls=[0]: (calls.__setitem__(0, calls[0] + 1)
                                                or (None if calls[0] == 1 else original_find(k)))
    result = backend.submit_report(payload, "+27820000002")
    assert result.success and result.reference == first.submitted_reference
    assert len(db.tables["reports"]) == 1


def test_status_authorisation_and_messages():
    db, backend, engine = make()
    db.tables["reports"].append({"id": 1, "ref": "EC-0001", "phone": "+27820000009", "status": "resolved",
                                 "updated_at": "2026-10-17T10:00:00+00:00", "duplicate_of": None})
    db.tables["reports"].append({"id": 2, "ref": "EC-0002", "phone": "0820000010", "status": "resolved",
                                 "updated_at": "2026-10-17T10:00:00+00:00", "duplicate_of": 1})
    db.tables["report_updates"].append({"report_id": 1, "created_at": "2026-10-17T10:00:00+00:00",
                                        "text": "Resolved · Roads · by Staff Person. Message sent: “Fixed today.”"})
    q = lambda ref, who, purpose="status": backend.get_report_status(  # noqa: E731
        StatusQuery(reference=ref, requester_id=who, channel="whatsapp", purpose=purpose))
    owner = q("ec-0001", "whatsapp:+27820000009")
    assert owner.found and owner.resolution_claim == "Fixed today."
    assert q("EC-0001", "whatsapp:+27820000010").found          # reported a linked duplicate
    assert not q("EC-0001", "whatsapp:+27829999999").found       # stranger
    assert not q("EC-0001", "whatsapp:+27829999999", "verification").found
    backend.open_verification = True
    assert q("EC-0001", "whatsapp:+27829999999", "verification").found
    assert not q("EC-9999", "whatsapp:+27820000009").found


def test_verification_saved_once_and_status_untouched():
    db, backend, engine = make()
    db.tables["reports"].append({"id": 1, "ref": "EC-0001", "phone": "+27820000001", "status": "resolved",
                                 "updated_at": "2026-10-17T10:00:00+00:00", "duplicate_of": None})
    chat = Chat(engine)
    for text in ("hi", "1", "3", "EC-0001", "2", "Still leaking"):
        chat.send(text)
    assert "has been recorded" in chat.text
    saved = db.tables["report_verifications"]
    assert len(saved) == 1 and saved[0]["stance"] == "dispute"
    assert db.tables["reports"][0]["status"] == "resolved"
    # The same response again (same key) is accepted without a second row
    from report_ec_ai.integration.schemas import VerificationPayload
    again = VerificationPayload.model_validate({**{k: saved[0][k] for k in ("idempotency_key", "language")},
                                                "reference": "EC-0001", "channel": "whatsapp",
                                                "stance_selected": "dispute", "statement_original": "x",
                                                "ai_advisory": saved[0]["ai_advisory"],
                                                "created_at": datetime.now(timezone.utc)})
    assert backend.submit_verification(again, "+27820000001").success
    assert len(saved) == 1


def test_session_store_round_trip_and_expiry():
    db = FakeDB()
    now = [datetime(2026, 10, 18, tzinfo=timezone.utc)]
    store = SupabaseSessionStore(db, now=lambda: now[0])
    engine = ConversationEngine(SupabaseBackend(db), store, now=lambda: now[0])
    Chat(engine, "whatsapp:+27820000001").send("hi")
    row = db.tables["bot_sessions"][0]
    assert row["phone"].startswith("whatsapp:") and "27820000001" not in str(row)
    assert store.get(row["phone"]) is not None
    now[0] = now[0].replace(day=20)
    assert store.get(row["phone"]) is None


def test_helpers():
    assert phone_digits("whatsapp:+27 82 000 0001") == phone_digits("0820000001") == "27820000001"
    assert resident_message("Done · by A B. Message sent: “Hello”") == "Hello"
    assert resident_message("no quote here") is None
    assert resident_message(None) is None
