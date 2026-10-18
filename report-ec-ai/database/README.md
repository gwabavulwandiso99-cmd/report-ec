# Database changes for the AI chatbot

`001_ai_chatbot.sql` lets the Supabase database hold what the AI chatbot
produces. It was written against the team's current schema (reports,
bot_sessions, report_updates, notifications, wards) and tested on a local
PostgreSQL 17 copy of those tables.

## What it changes

| Object | Change | Why |
|---|---|---|
| `reports.idempotency_key` | new column + unique index (only where not null) | a retried or double-tapped submission can never create a second report |
| `reports.location_text` | new column | the location exactly as the resident typed it |
| `reports.municipality` | new column | municipality named, recognised, or the pilot default |
| `reports.ai_suggestion` | new jsonb column | AI category/urgency, reasons, safety indicators, possible duplicates (advisory only) |
| `report_verifications` | new table, RLS on | resident confirm/dispute responses to a "fixed" claim, with the AI's advisory |
| `report_verification_summary` | new view (security_invoker) | confirms / disputes / contradiction flag per report, for the dashboard |
| `bot_sessions_updated_at_idx` | new index | quick clean-up of old chatbot sessions |

**Not changed:** every existing column, CHECK constraint, row, view and policy.
The dashboard and `main.py` keep working exactly as before. Old reports simply
have empty values in the new columns.

## How the chatbot fills the existing columns

| Column | Value |
|---|---|
| `ref` | database default (`EC-0001` …) |
| `phone` | resident's number without the `whatsapp:` prefix (same style as now) |
| `category` | AI suggestion mapped to the current list (`water_leak`, `no_water`, `power_outage`, `pothole`, `streetlight`, `other`) |
| `urgency` | AI suggestion; **low is stored as medium** because the CHECK constraint only allows critical/high/medium |
| `status` | `received` |
| `channel` | `whatsapp` or `ussd` |
| `suburb`, `landmark`, `ward`, `latitude`, `longitude`, `description`, `language` | from the conversation |
| `duplicate_of` | **never set by the AI**. Possible duplicates go into `ai_suggestion.possible_duplicates`; linking stays a staff action |

`bot_sessions` keeps its structure. The chatbot stores a **hashed** session key
in the `phone` column (`whatsapp:` + 32 hex characters), the state in `step` and
the conversation in `data`. Rows written by anyone else are never read, changed
or deleted by the chatbot.

The chatbot reads `report_updates` only to show residents the message staff
sent (the part after "Message sent:"). Staff names in that text are not shown.

## How to run it (Supabase)

1. Tell the database teammates first.
2. Supabase → **SQL Editor** → **New query**.
3. Paste all of `001_ai_chatbot.sql` and click **Run**. Expected: "Success. No rows returned".
4. Check: run the query at the bottom of the file (it's commented out). It should list the 4 new columns.

To undo: `001_ai_chatbot_rollback.sql` (deletes the verification responses; read
the warning in the file first).

## Using it from Python

```python
from db import supabase                                   # existing client (secret key)
from report_ec_ai.integration.supabase_backend import create_live_service

ai = create_live_service(supabase)                         # real database, not the mock
reply = ai.process_message({"channel": "whatsapp", "user_id": "whatsapp:+2782...", "text": "Hi"})
```

`SupabaseBackend(open_verification=False)`: only people who reported an issue
(or a report linked to it) can check its status or confirm/dispute the fix.
Set `open_verification=True` if the team decides any resident may verify.

## Possible later changes (team decision, not done here)

- Allow `low` in the `urgency` CHECK constraint (then update the dashboard and `taxonomy.json`).
- Add the guide's verification statuses (`pending_verification`, `verified_resolved`, `disputed`, `reopened`) to the `status` CHECK constraint, and have the verification workflow set them.
- Fill `ward` automatically from the coordinates with the `wards` table (PostGIS).
- A scheduled job that runs `SupabaseSessionStore.delete_expired()` once a day.
