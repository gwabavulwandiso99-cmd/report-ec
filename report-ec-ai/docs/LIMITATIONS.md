# Limitations and future work

Please say these honestly if judges or teammates ask.

## Language

- **isiXhosa text is a draft.** Nobody fluent has reviewed the messages or the
  vocabulary yet (see LANGUAGE_REVIEW.md). This is the most important open item.
- The system recognises about 265 isiXhosa words and stems for municipal
  problems. It does not understand isiXhosa grammar, idioms, sarcasm or
  spelling far from the listed forms.
- Prefix removal is a simple rule, so it can occasionally match the wrong word
  (e.g. stem *lahl-* "throw away" also starts *lahleka* "get lost").
- Only English and isiXhosa. No isiZulu, Sesotho or Afrikaans; no voice notes.
- Language detection is a word-count heuristic for short complaints. It is only
  reported; the reply language is always the resident's choice.

## Classification and priority

- **No trained model.** There is no real labelled data from Eastern Cape
  residents yet. The rules (word lists + weights + context rules) are the model.
  Their only measurements are on examples we wrote ourselves: 97% category / 87%
  priority on a small hold-out set (TESTING.md). That is not real-world evidence.
- Confidence labels and `evidence_score` are rule-based, not calibrated
  probabilities.
- The priority engine only knows what the resident wrote. It does not know the
  real size of a leak, the traffic on a road or how many households are affected.
- Known misses: phrases like "in the dark", "lamp pole", "manhole lid stolen".
- Category and urgency are always **suggestions** for staff.

### Plan for a trained model (after the hackathon)
1. With consent and the municipality's approval, collect real reports and remove personal data.
2. Let municipal staff label category and urgency (two people per report, to check agreement).
3. Split by **time** (train on older months, test on newer months) so the test is not leaked into training; keep duplicates of the same incident in the same split.
4. Train TF-IDF (word + character n-grams) with logistic regression; compare with the rules on the same test set; report per-language results.
5. Version the model (file name + data date) and keep the rules as a fallback when the model is unsure.

## Duplicates

- Text similarity uses concept words and TF-IDF; it can miss duplicates written
  very differently and can suggest look-alike reports that are different incidents.
- Without coordinates, location matching depends on area names and a small
  landmark list.
- Weights and thresholds were set by judgement, not learned.
- The AI only suggests; staff link duplicates.

## Verification

- Stance comes from wording only (a cue list with simple negation handling).
- Photos are never analysed. "Mentions evidence" only means the resident said
  they have a photo, or attached one.
- The AI never decides whether something is fixed. Who may verify, how many
  responses are needed and who inspects disputes are decisions for the team and
  the municipality.

## Chatbot and integration

- **Not connected** to real WhatsApp, USSD or Supabase. The mock backend is for
  demos and tests only (its references start with `MOCK-`).
- USSD screens are tested for length (≤ 182 characters) but not with a real
  USSD provider or phone. USSD providers end idle sessions quickly (often within
  2–3 minutes); a new dial starts a fresh menu.
- The JSON-file session store suits one server. Several servers need a
  database-backed store (interface in INTEGRATION.md).
- The area list covers part of Nelson Mandela Bay only. Unknown places are kept
  as written and marked for staff review. Ward numbers are never guessed.
- Safety messages give general advice and emergency numbers (112 from a
  cellphone, 10177 ambulance and fire). They never say that help was sent.
  The municipality should confirm whether a local emergency number should be added.
- Storage is not tamper-proof or immutable; that would be the database team's work.

## Privacy

- Session keys are salted hashes of phone numbers; set `REPORT_EC_SESSION_SALT`
  in production.
- The phone number still reaches the backend adapter, because the backend needs
  it to send updates. Its storage and access rules are Member 5's and the DB team's.
- Public trend summaries combine areas with fewer than 3 reports. With very
  small numbers overall, even combined counts can reveal something; review before
  publishing.
