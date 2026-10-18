# Going live: database + WhatsApp chatbot

What has to happen, in order, before a real phone can report a problem on
WhatsApp and see it on the dashboard.

| # | Step | Who | How we know it worked |
|---|---|---|---|
| 1 | Run `database/001_ai_chatbot.sql` in the Supabase SQL editor | DB side | "Success. No rows returned" |
| 2 | `python report-ec-ai/scripts/check_database.py` (read-only check) | anyone | "Database is ready for the AI chatbot." |
| 3 | Add the chatbot lines to `main.py` (below) | backend | server starts, `/docs` shows `POST /webhook/whatsapp` |
| 4 | Add `TWILIO_AUTH_TOKEN` and `PUBLIC_BASE_URL` to `.env` | backend | – |
| 5 | Make the laptop reachable from the internet (ngrok or cloudflared) | backend | an `https://…` address |
| 6 | Twilio Console → WhatsApp **Sandbox settings** → "When a message comes in" = `https://…/webhook/whatsapp`, method POST | Twilio account owner | – |
| 7 | Each test phone sends the sandbox **join code** to the Twilio sandbox number | testers | Twilio replies that you joined |
| 8 | Send "Hi" on WhatsApp and report a problem | testers | a reference number `EC-…` comes back and the report appears on the dashboard |

## Step 3: the lines for main.py

Under the existing imports (`from notify import send_whatsapp`):

```python
import sys
from pathlib import Path

# AI chatbot (Member 6). Its code is in the report-ec-ai folder.
sys.path.insert(0, str(Path(__file__).resolve().parent / "report-ec-ai"))
from report_ec_ai.integration.supabase_backend import create_live_service  # noqa: E402
from report_ec_ai.integration.twilio_whatsapp import create_whatsapp_router  # noqa: E402
```

Just **above** the `app.mount("/dashboard", ...)` line (the mount must stay last):

```python
ai = create_live_service(supabase)
app.include_router(create_whatsapp_router(ai))
```

The webhook is deliberately **not** behind the staff login (Twilio can't log
in). It checks Twilio's signature on every request instead, and refuses all
requests (503) if `TWILIO_AUTH_TOKEN` or `PUBLIC_BASE_URL` is missing.

## Step 4: .env

```
TWILIO_AUTH_TOKEN=...            # Twilio Console -> Account info (keep it secret)
PUBLIC_BASE_URL=https://abc123.ngrok-free.app   # from step 5, no slash at the end
```

`TWILIO_ACCOUNT_SID` and `TWILIO_WHATSAPP_FROM` are only needed by `notify.py`
(messages staff send from the dashboard). The chatbot answers inside Twilio's
own reply, so it doesn't need them.

`WHATSAPP_SKIP_SIGNATURE=1` turns the signature check off. Use it only for a
local test without Twilio, never with a public address.

## Step 5: a public address for the laptop

Twilio needs an `https` address it can reach. While the server runs on
`127.0.0.1:8000`, in a second terminal:

- **ngrok** (free account): `ngrok http 8000`
- or **cloudflared** (no account): `cloudflared tunnel --url http://localhost:8000`

Copy the `https://…` address into `PUBLIC_BASE_URL` and into the Twilio sandbox
settings. Restart the server after changing `.env`. The address changes every
time the tunnel restarts (unless you have a fixed ngrok domain), so both places
must be updated each time.

## Known limits of the sandbox

- Only phones that sent the join code can talk to the bot.
- Twilio's sandbox and WhatsApp limit messages outside the 24-hour window after
  the resident's last message. Replies to the resident are inside that window.
  Later status updates from the dashboard need approved templates for production.
- The isiXhosa wording is still a draft (docs/LANGUAGE_REVIEW.md).

## What has been tested (and what hasn't)

Tested locally on a PostgreSQL copy of the team's tables with the migration
applied, using signed requests built exactly like Twilio's (our signature code
matches Twilio's official Python library): an isiXhosa report with a photo
saved with a real `EC-…` reference; Twilio retries ignored; the report visible
through the dashboard API; staff marking it resolved; the resident disputing
on WhatsApp; the dispute stored with a contradiction flag and the status left
unchanged; unsigned requests rejected.

**Not tested yet:** the real Supabase project, a real Twilio account and a
real phone. Steps 1–8 above are that test.
