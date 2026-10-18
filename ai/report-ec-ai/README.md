# Report EC – AI/ML and multilingual chatbot component (Member 6)

This package is Component 7 of Report EC: the conversation logic of the resident
chatbot and the language intelligence behind it. It works in **English,
isiXhosa and mixed (code-switched) messages**, and it is built so the same
chatbot can run on WhatsApp, the community app and USSD.

It does **not** contain the database, the WhatsApp/USSD connections or the
dashboards. Those belong to other team members. This package talks to them
through small, documented interfaces (see `docs/INTEGRATION.md`).

Everything runs locally with no paid services and no LLM.

---

## What it does

| Module | What it does | Main file |
|---|---|---|
| A. Chatbot | Guided reporting, status check, confirm/dispute a repair, language switch, help/back/cancel/restart, resume after interruption | `chatbot/conversation.py` |
| B. Language | Clean-up of spelling, English + isiXhosa + code-switched vocabulary matching, language detection, location hints | `language/` |
| C. Classification and priority | Suggested category, subcategory and urgency (critical/high/medium/low) with reasons, confidence, safety indicators and a human-review flag | `intelligence/categorization.py`, `prioritization.py`, `analyzer.py` |
| D. Duplicates | Finds reports that *may* be the same incident (text meaning + category + location + time). Never merges or deletes | `intelligence/duplicates.py` |
| E. Verification triage | Reads resident confirm/dispute responses, flags contradictions with a municipal "fixed" claim. Never sets a status | `intelligence/verification.py` |
| F. Trends | Privacy-conscious JSON summaries for dashboards | `intelligence/trends.py` |

All AI outputs are labelled **advisory**. Staff make the decisions.

## What is implemented and what is not

**Implemented and tested (145 automated tests, all passing on Python 3.13 and 3.14):**
- Complete reporting conversations in English, isiXhosa and code-switched text
- USSD version of the flow (short screens, problem menu, no photo step; every screen on the tested paths is ≤ 182 characters)
- Rule-based categoriser and priority engine with explanations
- Duplicate suggestions with TF-IDF cosine similarity (scikit-learn, with a built-in fallback)
- Verification triage and trend summaries
- Session store interface + in-memory store + JSON-file store
- Idempotent submission (no double reports), no reference shown without backend confirmation
- Mock backend, callback backend adapter, Pydantic schemas, optional FastAPI router with an internal key

**Not implemented (other members or future work):**
- The real backend adapter that writes to Supabase (Member 5)
- The WhatsApp Cloud API webhook and the USSD provider callback (Member 5 / channel owner)
- A trained machine-learning model: there is no real labelled data yet, so the rules are the model (see `docs/LIMITATIONS.md`)
- Photo analysis: the AI never looks at photos
- Review of the isiXhosa wording by a fluent speaker (**must happen before real use**, see `docs/LANGUAGE_REVIEW.md`)

## Project structure

```
report-ec-ai/
├── report_ec_ai/
│   ├── chatbot/          conversation.py  states.py  intents.py  session_store.py  prompts.py  ussd.py
│   ├── language/         normalization.py  vocabulary.py  detection.py  locations.py
│   │   └── resources/    en.json  xh.json  lexicon.json
│   ├── intelligence/     analyzer.py  categorization.py  prioritization.py  duplicates.py
│   │                     verification.py  trends.py  guards.py
│   ├── integration/      schemas.py  backend_adapter.py  service.py  router.py
│   ├── config/           taxonomy.json  statuses.json  places.json  __init__.py
│   └── data/             sample_complaints.json  synthetic_eval.json  synthetic_holdout.json
├── tests/                one test file per module (+ helpers.py, conftest.py)
├── docs/                 ARCHITECTURE  INTEGRATION  LANGUAGE_REVIEW  TESTING  LIMITATIONS
├── demo.py               the 7 demonstrations
├── chat_cli.py           chat with the bot in the terminal
├── local_api.py          run the FastAPI router on its own (mock backend)
├── evaluate.py           measure the rules on the synthetic examples
├── requirements.txt
└── pytest.ini
```

Two small changes from the suggested layout, on purpose: `tests/` and `docs/`
sit next to the package instead of inside it (the normal Python layout, so the
tests are not shipped as part of the package), and the JSON settings live in
`report_ec_ai/config/` so teammates can change categories, statuses and place
names without touching Python code.

## Setup on Windows (PowerShell, VS Code)

1. Open the `report-ec-ai` folder in VS Code and open a terminal (**Terminal → New Terminal**).
2. Create and switch on a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   You should see `(.venv)` at the start of the line. If PowerShell says
   scripts are disabled, run this once and try again:
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```
3. Install the packages:
   ```powershell
   pip install -r requirements.txt
   ```

## Run the demo

```powershell
python demo.py        # all 7 demos
python demo.py 2      # only demo 2 (isiXhosa conversation)
```

## Chat with the bot yourself

```powershell
python chat_cli.py          # WhatsApp-style
python chat_cli.py --ussd   # USSD-style screens
```
Inside the chat: `/photo` sends a pretend photo, `/pin` a pretend location,
`/fail` makes the next submission fail, `/claim EC-1042` creates a mock report
that the municipality "says is fixed" so you can test disputes.

## Run the tests

```powershell
python -m pytest
```
Expected: `145 passed`. See `docs/TESTING.md` for what each test covers.

## Use it from Python

```python
from report_ec_ai.integration.service import ReportEcAIService

ai = ReportEcAIService()            # uses the MOCK backend unless you pass your own
reply = ai.process_message({"channel": "whatsapp", "user_id": "27820000001", "text": "Hi"})
print(reply.messages)

analysis = ai.analyze_complaint("Kukho water leak kufuphi neclinic.")
print(analysis.category.category, analysis.priority.priority, analysis.priority.explanation)
```

## Use the FastAPI router

```powershell
$env:REPORT_EC_AI_INTERNAL_KEY = "choose-a-long-random-value"
uvicorn local_api:app --reload --port 8001
```
Open http://127.0.0.1:8001/docs and send the header `X-Internal-Key` with every request.
Without a configured key every endpoint answers 503 on purpose.

In the real backend, Member 5 mounts the router instead (see `docs/INTEGRATION.md`).

## Integration

Read `docs/INTEGRATION.md`. In short, Member 5 needs to:
1. write a backend adapter (3 functions: submit report, get status, submit verification) on top of Supabase,
2. call `process_message` from the WhatsApp webhook and `process_ussd` from the USSD callback,
3. store sessions persistently (a database-backed `SessionStore`, or the JSON-file store for the demo).

## Known limitations

See `docs/LIMITATIONS.md`. The most important: the isiXhosa wording has not
been checked by a fluent speaker yet; the classifier is a word-list model
measured only on examples we wrote ourselves; and the AI never verifies
photos or physical repairs.
