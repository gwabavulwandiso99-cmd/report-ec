# Integration guide for Member 5 (backend)

This is everything the main FastAPI backend needs to use the AI component.
Nothing in this package connects to Supabase, WhatsApp or USSD by itself: those
connections are yours. **What is live and what is mocked:**

| Piece | State |
|---|---|
| Chatbot, language layer, classification, priority, duplicates, verification triage, trends | Implemented and tested |
| `MockBackendAdapter` (references like `MOCK-EC-1001`) | Mock, for demos and tests only |
| Real backend adapter on Supabase (`SupabaseBackend`) | **Implemented**, tested on a local PostgreSQL copy of the team's tables. Needs `database/001_ai_chatbot.sql` run on Supabase |
| Database session store on `bot_sessions` (`SupabaseSessionStore`) | **Implemented**, same tests |
| WhatsApp webhook (Twilio, as used by `notify.py`) | **Implemented** in `integration/twilio_whatsapp.py`, tested locally with signed requests. Still needs the `main.py` lines, a public tunnel and the Twilio sandbox URL: see `docs/WHATSAPP_SETUP.md` |
| USSD provider callback | **To do (Member 5)** – example below |

Quick start with the real database:

```python
from db import supabase
from report_ec_ai.integration.supabase_backend import create_live_service
ai = create_live_service(supabase)
```

Sections 3 and 4 describe the contract that `SupabaseBackend` and
`SupabaseSessionStore` already follow; read them if you need to change either.

---

## 1. Install into the main backend

Copy the `report_ec_ai/` folder into the backend repository (next to `main.py`),
and add these to the backend's `requirements.txt` if they are not there yet:
`pydantic`, `scikit-learn`, `numpy`.

```python
from report_ec_ai.integration.service import ReportEcAIService
from report_ec_ai.chatbot.session_store import JsonFileSessionStore

ai = ReportEcAIService(
    backend=SupabaseReportBackend(),             # you write this, see section 3
    session_store=JsonFileSessionStore("bot_sessions"),   # or your database store
)
```

Environment variables:

| Variable | Used for |
|---|---|
| `REPORT_EC_AI_INTERNAL_KEY` | Key for the optional `/ai/...` router. No key = router answers 503 |
| `REPORT_EC_SESSION_SALT` | Secret salt for hashing phone numbers into session keys. Set it once and never change it (changing it starts everyone's conversation again) |

Put both in `.env`. Never put them, or the Supabase secret key, in the
frontend, in chatbot replies or on GitHub.

## 2. Python functions you can call

| Function | Input | Output |
|---|---|---|
| `ai.process_message(msg)` | `IncomingMessage` or dict | `BotReply` |
| `ai.process_ussd(session_id, phone, text)` | provider fields | `"CON ..."` or `"END ..."` string |
| `ai.analyze_complaint(text or dict)` | `AnalyzeComplaintRequest` | `ComplaintAnalysis` |
| `ai.suggest_duplicates(new, existing)` | `ReportForMatching` + list | `DuplicateResult` |
| `ai.analyze_verification(request)` | `VerificationRequest` | `VerificationAnalysis` |
| `ai.trend_summary(request)` | `TrendRequest` | dict (JSON) |

All models are in `report_ec_ai/integration/schemas.py`.

## 3. The backend adapter (the most important part)

The chatbot calls three methods. Write a class like this (or use
`CallbackBackendAdapter` with three plain functions):

```python
from report_ec_ai.integration.backend_adapter import BackendAdapter
from report_ec_ai.integration.schemas import (
    SubmissionResult, StatusResult, VerificationSubmitResult)

class SupabaseReportBackend(BackendAdapter):
    def submit_report(self, payload, reporter_id):
        # 1. If a report with payload.idempotency_key exists, return ITS ref (no new row)
        # 2. Otherwise insert the report, get the real ref from the database
        # 3. Only return success=True when the insert really worked
        ...
        return SubmissionResult(success=True, reference=ref, status="received")

    def get_report_status(self, query):
        # Return found=False if the report does not exist OR the requester may not see it
        ...
        return StatusResult(found=True, authorized=True, reference=ref, status=row["status"],
                            updated_at=row["updated_at"], municipal_note=None,
                            resolution_claim=claim_text_or_None)

    def submit_verification(self, payload, requester_id):
        # Store the response once per payload.idempotency_key. Do NOT change the report status here.
        ...
        return VerificationSubmitResult(success=True)
```

### Rules the adapter must follow

1. **Idempotency.** The same `idempotency_key` must never create two reports.
   The chatbot reuses the key on every retry (double taps, timeouts, lost
   responses). Suggested database change for the DB team: a unique
   `idempotency_key` column on `reports`. If a retry arrives with the same key
   but edited details, keep the first report and either ignore or record the
   newer details as an update; never create a second report.
2. **No fake success.** Return `success=False` if anything failed. Raising an
   exception is also fine; the chatbot catches it and tells the resident the
   report was NOT sent.
3. **The reference comes from the database**, never from the AI. The chatbot
   shows it exactly as returned (1–40 characters: letters, digits, `-`, `_`, `/`).
4. **Status authorisation is yours.** The chatbot passes `requester_id` (the
   resident's phone number) and `purpose` (`"status"` or `"verification"`).
   Suggested rule: for `status`, only the phone number that reported it (or
   reported a linked duplicate); for `verification`, decide as a team (e.g.
   residents who reported in the same area). If not allowed, return
   `found=False` so nobody can find out which references exist.
5. **Statuses.** Return either the guide's lifecycle (`submitted`,
   `acknowledged`, `in_progress`, `pending_verification`, `verified_resolved`,
   `disputed`, `reopened`) or the dashboard's current values (`received`,
   `awaiting_dispatch`, `team_assigned`, `in_progress`, `resolved`). They are
   mapped in `config/statuses.json`. Note that the dashboard's `resolved` is
   treated as **pending verification** (a municipal claim, not a verified fix).
   The chatbot only offers confirm/dispute when the status is
   `pending_verification` or `disputed` (`resolved` counts as pending).
6. **Resolution claim.** When staff mark a report fixed, store their message
   and return it as `resolution_claim`. Residents see it before they confirm or
   dispute.

### Payload the chatbot sends: `submit_report(payload, reporter_id)`

`reporter_id` is the resident's phone number (WhatsApp `from` / USSD
`phoneNumber`). It is a separate argument so the payload itself can be logged
without personal data.

```json
{
  "idempotency_key": "fc50416d6349493c939896924c44a3c8",
  "channel": "whatsapp",
  "language": "en",
  "description_original": "There is a burst water pipe near the clinic.",
  "description_source": "resident_text",
  "resident_selected_category": null,
  "location": {
    "municipality": "Nelson Mandela Bay",
    "municipality_source": "recognised_area",
    "area": "Zwide",
    "landmark": "clinic",
    "location_text": "Zwide",
    "latitude": null,
    "longitude": null,
    "ward": null,
    "needs_review": false
  },
  "evidence": [{"media_id": "wamid.HBgM...", "mime_type": "image/jpeg", "caption": null}],
  "ai_suggestion": {
    "category": "water",
    "subcategory": "burst_or_leak",
    "category_confidence": "high",
    "legacy_category": "water_leak",
    "priority": "high",
    "priority_confidence": "medium",
    "legacy_urgency": "high",
    "explanation": "Words linked to 'water': burst, water pipe (score 2.0). Starting level for water/burst_or_leak: medium. A school, clinic or vulnerable people are affected.",
    "safety_indicators": [],
    "needs_human_review": true,
    "review_reasons": ["Suggested urgency is high."],
    "advisory_only": true,
    "model_version": "rules-2026.10-v1"
  },
  "created_at": "2026-10-18T00:03:43.756011Z"
}
```

Field notes:
- `description_original` is the resident's text (control characters removed,
  nothing else changed). Store it unchanged.
- `description_source = "ussd_menu"` means the resident picked a menu option on
  USSD and the text is the option label.
- `location.location_text` is what the resident typed. `needs_review: true` means
  the place was not recognised or the pin is outside the Eastern Cape.
- `municipality_source = "pilot_default"` means the resident did not name one
  and the pilot municipality was used.
- `evidence` holds WhatsApp media ids only. Download the photo with the
  WhatsApp Media API, store it (e.g. Supabase Storage) and save the URL.
- `ai_suggestion` must be stored and shown **as a suggestion**. Staff set the
  real category/urgency. `priority` can be `null` (not enough information).

### Mapping to the current `reports` table (as the dashboard uses it today)

| Column | From the payload |
|---|---|
| `ref` | generated by the database |
| `phone` | `reporter_id` |
| `language` | `language` |
| `description` | `description_original` |
| `category` | `ai_suggestion.legacy_category` (until the DB team adopts the new taxonomy) |
| `urgency` | `ai_suggestion.legacy_urgency` (low is sent as medium until the dashboard supports low) |
| `suburb` | `location.area` |
| `landmark` | `location.landmark` or `location.location_text` |
| `ward` | `location.ward` |
| `latitude`, `longitude` | `location.latitude`, `location.longitude` |
| `photo_url` | after downloading `evidence[0]` |
| `status` | `received` |

Migration `database/001_ai_chatbot.sql` adds `idempotency_key` (unique),
`ai_suggestion` (jsonb), `location_text` and `municipality`, plus the
`report_verifications` table. See `database/README.md`.

### Verification payload: `submit_verification(payload, requester_id)`

```json
{
  "idempotency_key": "9b1e…",
  "reference": "EC-1042",
  "channel": "whatsapp",
  "language": "en",
  "stance_selected": "dispute",
  "statement_original": "The pipe is still leaking. I took another photograph today.",
  "evidence": [{"media_id": "wamid.P2", "mime_type": "image/jpeg", "caption": null}],
  "ai_advisory": {
    "contradiction_detected": true,
    "needs_human_review": true,
    "explanation": "Possible contradiction: the municipality claims the problem is fixed, but 1 response(s) say it is not. …",
    "changes_status": false,
    "advisory_only": true
  },
  "created_at": "…"
}
```

The verification workflow (not the AI) decides when a report becomes
`verified_resolved`, `disputed` or `reopened`.

### Status answer: `get_report_status(query)`

```json
{"found": true, "authorized": true, "reference": "EC-1042", "status": "pending_verification",
 "updated_at": "2026-10-15T10:00:00Z", "municipal_note": null,
 "resolution_claim": "The burst pipe has been repaired."}
```

## 4. Sessions

The engine needs a `SessionStore` with `get(key)`, `save(session)` and
`delete(key)`. Keys are already hashed; the session holds no phone numbers.
`session.model_dump_json()` / `Session.model_validate_json(text)` turn a session
into text and back.

Sketch for a table-backed store (adapt the table name and columns to whatever
the DB team decides; the existing `bot_sessions` table may already fit):

```python
class SupabaseSessionStore(SessionStore):
    def get(self, key):
        rows = supabase.table("bot_sessions").select("data").eq("session_key", key).execute().data
        return Session.model_validate_json(rows[0]["data"]) if rows else None

    def save(self, session):
        supabase.table("bot_sessions").upsert(
            {"session_key": session.key, "data": session.model_dump_json(),
             "updated_at": session.updated_at.isoformat()}).execute()

    def delete(self, key):
        supabase.table("bot_sessions").delete().eq("session_key", key).execute()
```

Delete rows older than 24 hours with a scheduled job.

## 5. Connecting WhatsApp

The project sends WhatsApp messages through **Twilio** (`notify.py`). With
Twilio, incoming messages arrive as a form POST with `From`
(`whatsapp:+27…`), `Body`, `MessageSid`, `NumMedia`, `MediaUrl0`,
`MediaContentType0`, `Latitude` and `Longitude`. Map them like this:

```python
@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    # 1. Check the X-Twilio-Signature header with the Twilio auth token (Member 5)
    incoming = {"channel": "whatsapp", "user_id": form["From"], "message_id": form.get("MessageSid"),
                "text": form.get("Body") or None}
    if int(form.get("NumMedia", 0)) > 0:
        incoming["media"] = [{"media_id": form["MediaUrl0"], "mime_type": form["MediaContentType0"]}]
    if form.get("Latitude"):
        incoming["location"] = {"latitude": float(form["Latitude"]), "longitude": float(form["Longitude"])}
    reply = ai.process_message(incoming)
    for text in reply.messages:
        send_whatsapp(form["From"], text)
    return PlainTextResponse("")          # or reply with TwiML instead of send_whatsapp
```

A photo's `MediaUrl0` is stored in `reports.photo_url` by `SupabaseBackend`.

If the team later moves to the Meta WhatsApp Cloud API instead, the mapping is:

Example webhook (not included as working code because it depends on your
token, phone number id and the existing `notify.send_whatsapp`):

```python
@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    body = await request.json()
    # 1. Verify the X-Hub-Signature-256 header with your app secret (Member 5)
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                incoming = {"channel": "whatsapp", "user_id": msg["from"], "message_id": msg["id"]}
                if msg["type"] == "text":
                    incoming["text"] = msg["text"]["body"]
                elif msg["type"] == "image":
                    incoming["media"] = [{"media_id": msg["image"]["id"],
                                          "mime_type": msg["image"]["mime_type"],
                                          "caption": msg["image"].get("caption")}]
                    incoming["text"] = msg["image"].get("caption")
                elif msg["type"] == "location":
                    incoming["location"] = {"latitude": msg["location"]["latitude"],
                                            "longitude": msg["location"]["longitude"]}
                reply = ai.process_message(incoming)
                for text in reply.messages:
                    send_whatsapp(msg["from"], text)
    return {"ok": True}
```

- Answer Meta quickly (return 200). If replies are slow, process in a
  background task.
- Repeated deliveries are safe: the same `message_id` is ignored.
- The webhook must stay **outside** the staff login check (as in the current
  `main.py`), but verify Meta's signature.
- Replies go out inside WhatsApp's 24-hour customer-service window because the
  resident just wrote to us. Later status updates need approved templates.

## 6. Connecting USSD

For a provider like Africa's Talking (form fields `sessionId`, `phoneNumber`, `text`):

```python
@app.post("/ussd", response_class=PlainTextResponse)
async def ussd(sessionId: str = Form(...), phoneNumber: str = Form(...), text: str = Form("")):
    return ai.process_ussd(sessionId, phoneNumber, text)
```

USSD behaviour: a fresh menu on each dial, a numbered list of common problems
(or type your own), no photo step, short screens (all ≤ 182 characters, tested),
`END` after submission/cancel/status. Not tested with a real provider yet.

## 7. Optional FastAPI router

```python
from report_ec_ai.integration.router import create_router
app.include_router(create_router(ai))      # reads REPORT_EC_AI_INTERNAL_KEY
```

| Method & path | Body | Response |
|---|---|---|
| GET `/ai/health` | – | `{"ok": true, "mock_backend": false}` |
| POST `/ai/chat/message` | `IncomingMessage` | `BotReply` |
| POST `/ai/chat/ussd` | `{"session_id","phone_number","text"}` | text `CON …` / `END …` |
| POST `/ai/complaints/analyze` | `{"text": "...", "language_hint": "xh"}` | `ComplaintAnalysis` |
| POST `/ai/complaints/duplicates` | `{"new_report": {...}, "existing_reports": [...]}` | `DuplicateResult` |
| POST `/ai/verification/analyze` | `{"municipal_claim": "...", "statements": [...]}` | `VerificationAnalysis` |
| POST `/ai/trends/summary` | `{"reports": [...], "data_label": "live", "audience": "public"}` | summary JSON |

Every request needs `X-Internal-Key`. Errors: 401 wrong/missing key, 503 no key
configured, 422 invalid body (Pydantic message). These routes are for the
backend only. Do not call them from a browser, because the key would be visible.

## 8. Error handling summary

| Situation | What the chatbot does |
|---|---|
| Adapter returns `success=False`, raises, times out, or gives no valid reference | Says the report was NOT sent, keeps the draft, offers retry (same idempotency key) |
| Status lookup fails | "Can't check the status right now", back to menu |
| Report not found / not allowed | Same message for both |
| Verification save fails | Says it was NOT saved, offers retry |
| Malformed message (bad channel, empty user id, too long) | `IncomingMessage` validation error, so return 422 or log and ignore |
| Message over 1000 characters | Asks the resident to shorten it |
| Repeated webhook message id | Empty reply with `duplicate_message: true`; send nothing |

## 9. How the dashboards can use the AI output

Municipal dashboard (Member 4):
- Show `ai_suggestion.category/priority` with an **"AI suggestion"** label and
  `explanation`, next to the staff-set values. Staff can accept or change them.
- Highlight `safety_indicators` and `needs_human_review`.
- Show possible duplicates from `/ai/complaints/duplicates` with their reasons
  and a "link as duplicate" button. Linking stays a staff action (it sets
  `duplicate_of`, nothing is deleted).
- On a verification-pending report, show the `ai_advisory` of each resident
  response; contradictions first.
- Add `low` to the urgency list and the verification statuses to the status list.

Community dashboard (Member 3):
- Use `/ai/trends/summary` with `audience: "public"`: no references, small
  areas combined, no personal data. Always display `data_label`.
