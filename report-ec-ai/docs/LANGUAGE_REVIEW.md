# isiXhosa language review

**Status: NOT YET REVIEWED.** All isiXhosa text in this package was drafted by
the development team. It has not been checked by a fluent isiXhosa speaker.
It must be reviewed before real residents use the chatbot.

The package does **not** "understand" isiXhosa. It recognises a limited list of
municipal-service words and stems (about 265 isiXhosa words and stems, plus about 80 common words used only to guess the language). When it is
unsure, the chatbot asks the resident to choose from a menu instead of guessing.

## How to review

1. Run `python scripts/language_review_sheet.py`. It creates
   `language_review_sheet.csv` with every chatbot message (English and the
   isiXhosa draft side by side) and every isiXhosa vocabulary entry.
2. Ask a fluent speaker (ideally someone from Nelson Mandela Bay, so the
   wording matches local usage) to fill in the "OK?", "Corrected isiXhosa" and
   "Notes" columns.
3. Put the corrections into `report_ec_ai/language/resources/xh.json` and
   `lexicon.json`, then run `python -m pytest`. The tests check that both message
   files still have the same keys and the same `{placeholders}`.

## Messages that most need checking

These carry safety, legal or accountability meaning, so a wrong word matters more.

| Key | Draft | What to check |
|---|---|---|
| `safety.footer` | "…Le ncoko AYIZazisi iinkonzo zongxamiseko." | Must clearly say this chat does **not** alert emergency services. Check the verb form "ayizazisi" and the capitalisation used for emphasis. |
| `safety.electrical` | "Hlala kude neentambo … Musa ukuchukumisa nantoni na edibene nazo." | Clear and natural? "edibene nazo" (connected to them) |
| `safety.flooding` | "…ucime izixhobo zombane ezinokumanzi…" | "ezinokumanzi" (that may get wet) is the weakest form here |
| `submission_failed` | "…AYITHUNYELWANGA kwaye akukabikho nombolo yesalathiso." | Must clearly say the report was **not** sent |
| `verify.recorded` | "…Lo myalezo awuyivali ingxelo." | Must say the response does **not** close the report |
| `status.pending_verification.label` | "Kuthiwa ilungisiwe – ilindele ukuqinisekiswa luluntu" | Must show this is a **claim** by the municipality, not a confirmed fix |
| `status.verified_resolved.label` | "Iqinisekisiwe ukuba ilungisiwe" | |
| `summary` (last line) | "Uhlobo nokungxamiseka zizindululo kuphela…" | Must say category and urgency are **suggestions**; is "izindululo" understood? |
| `priority.*` | "Ingxamiseko enkulu kakhulu / ephezulu / ephakathi / ephantsi" | Natural way to express four urgency levels? |
| `ask_location` | "…kunye nendawo eyaziwayo ekufutshane…" | "landmark" phrasing; we avoided "uphawu" because it may be unclear |
| `location.landmark` | "indawo eyaziwayo: {landmark}" | |
| `landmark.*` | "Ikliniki", "Isikolo", "Isitishi sebhasi", "Ityaveni", "Imall" … | Some are loanwords; check local usage |
| `ussd.problem.*` | "Umbhobho ovuzayo", "Iintambo zombane ezixhomekileyo", "Ilindle liyaphuphuma" … | Short labels for USSD; each must fit on a small screen and also be recognised by the vocabulary (tests check this) |
| `category.public_infrastructure` | "Izakhiwo zoluntu ezonakeleyo" | "izakhiwo" means buildings; parks/benches/bus shelters may need a wider word |
| `help` | command words *imenyu, emva, rhoxisa, qala, ulwimi* | Are these the words residents would type? Add others to `chatbot/intents.py` |

## Vocabulary entries that need checking

In `lexicon.json`. The matcher removes common prefixes (e.g. *u-, um-, ama-,
isi-, e-, ne-, wa-, ku-, sa-*) and compares the rest with the stems, so it can
recognise *wamanzi*, *nekliniki*, *kusavuza* or *esikolweni*. Please check:

- **Ambiguous words.** *isibane* is any lamp (we treat it as a streetlight);
  *intambo* is rope/string/wire; *amanzi amdaka* (dirty water) is treated as
  sewage; *ingozi* can mean danger **or** accident; *umngxuma* is any hole (we
  treat it as a pothole); *yonke/bonke/wonke* (all) is used as a sign that many
  people are affected.
- **Stems that may match the wrong word.** *lahl-* (throw away, for dumping) also
  starts *lahleka* (get lost); *nuka* (smell); *phuk-* (broken).
- **Negatives and verification wording.** *akulungiswanga, ayilungiswanga,
  kusavuza, isekhona, isaphukile, abafikanga, khange bafike, ubuxoki* (dispute)
  and *kulungisiwe, ilungisiwe, iyasebenza, abuyile, enkosi* (confirm). Are there
  common ways of saying "it is still broken" or "they fixed it" that are missing?
- **Duration words.** *iintsuku/iiveki/iinyanga* with *ezimbini, ezintathu, ezine,
  ezintlanu…*. Other common forms?
- **Place names.** Area aliases in `config/places.json`, e.g. *eZwide, KwaZakhele,
  eMotherwell*. Add local isiXhosa names for areas.

## What the system does when it is not sure

- No recognised words or a tie between categories → the resident chooses a
  category from a numbered menu.
- Unknown area → the location is kept exactly as written and marked for staff review.
- The language guess never changes the reply language; only the resident's
  choice does.
- The resident's original words are always stored and shown unchanged.

## Not supported

isiZulu, Sesotho, Afrikaans or other languages; voice notes; spelling far from
the listed forms; long messages that mix several problems; sarcasm.
