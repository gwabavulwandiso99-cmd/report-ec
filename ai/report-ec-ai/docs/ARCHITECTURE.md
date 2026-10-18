# Architecture

## How the parts connect

```
 WhatsApp webhook ─┐                                   ┌─ BackendAdapter (Member 5)
 Community app ────┼─► IncomingMessage ─► ConversationEngine ─┤     submit_report
 USSD callback ────┘   (UssdGateway for USSD)    │            │     get_report_status
                                                 │            └─    submit_verification
                                                 ▼
                                         ComplaintAnalyzer
                         normalize ─► vocabulary ─► detect language
                                         │
                       categorization ─► prioritization ─► location hints
                                         │
                                  ComplaintAnalysis (advisory)

 Dashboard / backend jobs ─► DuplicateDetector, analyze_verification, summarize_trends
```

- The **chatbot never touches the database**. It hands a finished `ReportPayload`
  to a `BackendAdapter` and only shows a reference number if the adapter returns
  one.
- The **intelligence functions are pure**: they take data in and return a new
  result. They never change, merge or delete the input.
- `ReportEcAIService` (integration/service.py) bundles everything for Member 5,
  and `create_router()` exposes the same functions over FastAPI.

## Module B – language layer

1. `normalization.normalize()` keeps the original text and makes a cleaned copy:
   Unicode clean-up, control characters removed, lower case, accents removed
   (crèche → creche), hyphens inside words removed (i-clinic → iclinic),
   long letter repeats shortened (pleeease → pleease), SMS spellings fixed from a
   list (plz → please, watter → water) and a careful fuzzy correction for long
   English words only. Every correction is recorded.
2. `vocabulary.Vocabulary.match()` finds domain concepts in this order:
   multi-word phrases → whole words → isiXhosa stems after prefix removal
   (wamanzi → amanzi → manzi) → English loanwords inside an isiXhosa prefix
   (neclinic → clinic). Words used by a phrase are not reused.
3. `detection.detect_language()` counts English and isiXhosa clues and returns
   `en`, `xh`, `mixed` or `unknown` with a confidence. The chatbot always replies
   in the resident's **chosen** language; detection is only reported, never used
   to switch language by itself.
4. `locations.extract_location()` finds a known area, landmark and ward number.

All vocabulary is in `language/resources/lexicon.json`; all chatbot messages
are in `en.json` and `xh.json`.

## Module C – classification and priority

**Category.** Each matched concept adds its weight to a category. Phrases get a
small bonus. A hazard combination (cable + hanging/exposed) adds extra weight to
electricity. The subcategory is the first rule in `config/taxonomy.json` whose
concepts appear.

**Confidence (category):**

| Label | Rule | Meaning |
|---|---|---|
| high | top score ≥ 2.0 and second-best ≤ half of it | several clear words for one category |
| medium | top score ≥ 1.0 and clearly ahead | at least one clear word |
| low | anything else | no evidence, weak evidence or a tie → the chatbot asks the resident |

`evidence_score` = top / (top + second + 1). It shows how one-sided the evidence
is. It is **not a probability** and is not calibrated.

**Priority.** Starts from the subcategory's base level, then:
- lowered one step for routine wording (when possible, repaint, slow drip)
- raised for exposed wires, fire, open manholes, injury, sewage/flooding near
  homes, busy roads/accidents, a vulnerable place or people, many people
  affected, or a long outage (≥ 2 days) / long uncollected waste (≥ 7 days)
- **critical** only for danger to life together with a factor that makes harm
  likely (e.g. exposed wires near a school, an open manhole where children are,
  fire involving electrical equipment or homes)
- "urgent" from the resident can lift low to medium but never higher
- negated danger ("not dangerous", "akukho ngozi") is ignored
- no category and no danger signs → **no priority at all** (`None`), rather than a guess
- the location the resident typed can add place context (school, clinic, busy
  road) to the urgency, but never changes the category

**Priority confidence:** `high` when driven by explicit safety/routine words and
the category is clear, `low` when the category is uncertain, otherwise `medium`.

**Human review flag** is set for high/critical, any safety indicator, no
priority, low category confidence, unclear language or instruction-like text.

**Why rules and not a trained model?** There is no real, labelled set of
Eastern Cape complaints yet. Training on examples we wrote ourselves would only
teach the model our own word lists and would look better than it is. The rules
are explainable, easy for teammates to change in JSON, and work offline. The
`ComplaintAnalyzer` class can be subclassed to add a model later without
changing anything else (see LIMITATIONS.md for the plan).

## Module D – duplicates

Score = 0.45 × text + 0.25 × category + 0.20 × location + 0.10 × time.

- **Text** is TF-IDF cosine similarity on a "meaning document": concept ids
  (counted twice), the subcategory, and the normalised words. Because concepts
  are language-independent, "Burst pipe near the clinic" and "Kukho umbhobho
  wamanzi ogqabhukileyo kufuphi nekliniki" can match.
- **Hard exclusions:** different parent category, coordinates > 1 km apart,
  different named areas, more than 14 days apart.
- **Different named landmarks** (clinic vs school) multiply the score by 0.85.
- Labels: ≥ 0.70 `strong_candidate`, ≥ 0.50 `possible_candidate`. Every
  candidate says `requires_human_confirmation: true` and lists its reasons.
- A match with a report marked resolved is labelled `possible_recurrence`.

## Module E – verification triage

`analyze_verification()` reads each resident statement with a separate cue list
(still leaking, not fixed, akulungiswanga, kusavuza … / fixed, working now,
kulungisiwe, abuyile …), handles negation ("never fixed"), notes evidence
mentions, compares the resident's menu choice with their words, and flags a
**contradiction** when the municipality claims a fix and anyone disputes it.

There is no voting. One dispute triggers review even against many confirmations,
and confirmations alone only say the claim is *supported*. The output has
`changes_status: false` and never contains a status.

## Module F – trends

`summarize_trends()` reads only eight fields (reference, category, area,
municipality, status, created_at, duplicate_of, dispute_count); the input model
drops everything else. Public summaries combine areas with fewer than 3 reports
and never list report references. Legacy statuses are mapped to the guide's
lifecycle; the dashboard's `resolved` counts as **pending verification**.

## Chatbot state machine

```
LANGUAGE_SELECTION ─► MAIN_MENU ─┬─► AWAITING_DESCRIPTION ─► (AWAITING_CATEGORY_CLARIFICATION)
                                  │        ─► AWAITING_LOCATION ─► AWAITING_WARD ─► AWAITING_EVIDENCE*
                                  │        ─► AWAITING_CONFIRMATION ─┬─► SUBMITTING ─┬─► REPORT_SUBMITTED
                                  │                ▲                 │               └─► SUBMISSION_FAILED (retry)
                                  │                └── EDIT_MENU ◄───┘
                                  ├─► AWAITING_REFERENCE (status)
                                  └─► AWAITING_VERIFICATION_REFERENCE ─► _RESPONSE ─► _DETAILS (─► VERIFICATION_FAILED)
 any state ─► MAIN_MENU / CANCELLED / LANGUAGE_SELECTION (menu, cancel, restart, language)
 idle > 30 min with an unfinished report ─► RESUME_PROMPT
 * no photo step on USSD
```

`states.check_transition()` rejects any other move. The suggested
REVIEW_REPORT and AWAITING_CONFIRMATION states are one state here
(`AWAITING_CONFIRMATION` shows the summary and waits), and STATUS_LOOKUP /
VERIFICATION_FLOW are split into the concrete waiting states above.

Steps are skipped when the answer is already known: "Burst pipe near the clinic
in Zwide" skips the location question; a ward number in the text skips the ward
question; a photo sent earlier skips the photo question.

## Sessions

- Key: HMAC-SHA256 of `channel:user_id` with `REPORT_EC_SESSION_SALT`. Phone
  numbers are not stored in sessions or file names.
- The same number on WhatsApp and USSD has two separate sessions.
- Idle 30 min with an unfinished report → resume prompt. Drafts expire after
  24 h. Stores drop sessions untouched for 24 h.
- Repeated channel message ids (webhook retries) are ignored.
- `SessionStore` has three methods (get/save/delete). Included:
  `InMemorySessionStore`, `JsonFileSessionStore`.

## Submission safety

- An idempotency key (uuid4) is created the first time the resident presses
  Submit and **reused on every retry** of that report, even after an edit. If the
  backend saved the report but the answer was lost, the retry gets the same
  reference instead of a second report.
- A reference is shown only when `success=True` and a reference that looks
  valid comes back. Exceptions, timeouts, `success=False` or a missing
  reference all lead to "NOT sent, no reference yet" and a retry option.

## Privacy-conscious logging

The chatbot logs only masked session keys (e.g. `whatsapp:3fa9…`), state
changes and error types. It never logs message text, phone numbers or
locations.
