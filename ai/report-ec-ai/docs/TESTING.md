# Testing

## How to run

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest            # all tests
python -m pytest -v         # with test names
python -m pytest tests/test_chatbot.py -k isixhosa   # one group
python evaluate.py          # rules on the development examples
python evaluate.py --holdout
```

## Actual results

Run on 18 October 2026 in the development environment:

| Python | Result |
|---|---|
| 3.13.16 | **145 passed**, 0 failed (≈2 s) |
| 3.14.6 | **145 passed**, 0 failed (≈2 s) |

Libraries used for that run: pydantic 2.14.1, fastapi 0.143.3, scikit-learn 1.9.1,
numpy 2.5.4, pytest 9.1.1, httpx 0.28.1. `python demo.py` ran all 7 demos without
errors. The tests were run on Linux; the code uses no Linux-only features
(paths via `pathlib`, file names without `:`), but please run the tests once on
your Windows laptop as well.

| File | Tests | Covers |
|---|---|---|
| `test_chatbot.py` | 29 | conversations, commands, errors, idempotency, sessions |
| `test_languages.py` | 19 | normalisation, vocabulary, detection, prompt files |
| `test_classification.py` | 31 | categories in English, isiXhosa, mixed; unclear input |
| `test_priority.py` | 21 | urgency rules, safety, durations, context |
| `test_duplicates.py` | 11 | duplicate matching and false-positive checks |
| `test_verification.py` | 11 | dispute triage and the chatbot verification flow |
| `test_trends.py` | 6 | counts, legacy statuses, privacy |
| `test_integration.py` | 17 | backend contract, status authorisation, USSD, router, file store |

## The 25 required tests

| # | Requirement | Test (file::name) |
|---|---|---|
| 1 | Complete English conversation | `test_chatbot.py::test_complete_english_conversation` |
| 2 | Complete isiXhosa conversation | `test_chatbot.py::test_complete_isixhosa_conversation` |
| 3 | Mixed English/isiXhosa complaint | `test_chatbot.py::test_code_switched_conversation` |
| 4 | Language switching | `test_chatbot.py::test_language_switch_mid_conversation_keeps_the_draft` |
| 5 | Invalid menu choices | `test_chatbot.py::test_invalid_menu_choices` |
| 6 | Missing description | `test_chatbot.py::test_missing_description` |
| 7 | Missing location | `test_chatbot.py::test_missing_location`, `test_unknown_place_is_kept_as_written` |
| 8 | Cancel and restart | `test_chatbot.py::test_cancel_and_restart`, `test_back_command` |
| 9 | Editing before confirmation | `test_chatbot.py::test_editing_before_confirmation` |
| 10 | Backend submission failure | `test_chatbot.py::test_backend_failure_is_not_reported_as_success`, `test_backend_exception_is_handled` |
| 11 | No fake reference number | `test_chatbot.py::test_no_reference_without_backend_confirmation` |
| 12 | Duplicate submission prevention | `test_chatbot.py::test_double_submit_creates_one_report`, `test_retry_after_lost_response_reuses_idempotency_key`, `test_repeated_webhook_delivery_is_ignored` |
| 13 | English category prediction | `test_classification.py::test_english_categories` (10 cases) |
| 14 | isiXhosa category prediction | `test_classification.py::test_isixhosa_categories` (7 cases) |
| 15 | Code-switched category prediction | `test_classification.py::test_code_switched_categories` (4 cases) |
| 16 | Safety-related priority | `test_priority.py::test_life_threatening_hazards_are_critical` (5 cases), `test_same_category_different_context_gives_different_priority` |
| 17 | Unclear complaint handling | `test_classification.py::test_unclear_complaints_ask_for_clarification`, `test_chatbot.py::test_unclear_complaint_asks_resident_to_choose` |
| 18 | Duplicate matching | `test_duplicates.py::test_related_reports_are_suggested` (includes an isiXhosa duplicate) |
| 19 | Different-location false positives | `test_duplicates.py::test_same_text_in_a_different_area_is_not_a_duplicate`, `test_same_text_far_apart_by_coordinates_is_not_a_duplicate`, `test_different_landmark_or_category_or_time_is_excluded` |
| 20 | Resident resolution dispute | `test_verification.py::test_resident_dispute_is_flagged_for_review`, `test_chatbot_dispute_flow_records_response_without_closing_report` |
| 21 | No automatic resolution | `test_verification.py::test_ai_never_sets_a_status`, `test_one_dispute_among_many_confirmations_still_needs_review` |
| 22 | Trend summary correctness | `test_trends.py::test_counts_are_correct` |
| 23 | Public data privacy | `test_trends.py::test_public_summary_contains_no_personal_data`, `test_integration.py::test_router_endpoints` |
| 24 | Session isolation | `test_chatbot.py::test_sessions_do_not_leak_between_residents`, `test_session_store_does_not_hold_phone_numbers` |
| 25 | Status lookup authorisation contract | `test_integration.py::test_status_lookup_only_for_the_reporter`, `test_status_query_sends_requester_id_to_backend` |

Extra edge cases: empty/None/punctuation-only text, control characters, 1 500-
and 5 000-character messages, wrong channel names, empty user ids, unsupported
file types, invalid coordinates, a damaged session file, session expiry and
resume, prompt-injection text, missing scikit-learn, USSD screen length on
every main path in both languages, router without a key, malformed router
bodies, and matching keys/placeholders between `en.json` and `xh.json`.

## Rule evaluation on synthetic examples (honest reading)

`python evaluate.py` measures the categoriser and the priority engine on
examples **we wrote ourselves**. These are not real reports.

**Development set** (`data/synthetic_eval.json`, 47 examples): 100% category,
100% priority (within the levels we accept) after fixes. This set was used
while developing: it exposed two real bugs (isiXhosa number words after
*iintsuku/iiveki* were not read; *kwindlela enkulu* did not match *indlela
enkulu*) which we then fixed. Because we fixed the rules while looking at this
set, **its score means nothing about real performance.**

**Hold-out set** (`data/synthetic_holdout.json`, 30 examples, written after the
rules were finished and not used for tuning):

| Set | n | Category correct | Priority acceptable | Asked resident to clarify |
|---|---|---|---|---|
| English | 15 | 93% | 80% | 4 |
| isiXhosa | 10 | 100% | 90% | 2 |
| Mixed | 5 | 100% | 100% | 0 |
| **All** | **30** | **97%** | **87%** | **6** |

Misses we left in on purpose (no tuning on the hold-out set):
- "The whole of Section 3 has been in the dark for two days": "in the dark" is not in the vocabulary, so the category was unknown and the bot would ask the resident to choose.
- "Lamp pole flickering on and off at night": "lamp pole" is not recognised as a streetlight, so it was read as a general electricity issue (medium instead of low).
- "Manhole lid stolen on the corner, someone could fall in": "manhole lid stolen" is not an "open manhole" phrase, so it came out medium instead of high.
- "Indlela inemingxuma emininzi": the plural *imingxuma* inside *inemingxuma* was not linked to potholes, so low instead of medium.

Even the hold-out numbers are optimistic: the same people wrote the examples
and the word lists, and the isiXhosa examples have not been checked by a fluent
speaker. Real performance can only be measured on real, consented, anonymised
reports labelled by municipal staff (see LIMITATIONS.md).
