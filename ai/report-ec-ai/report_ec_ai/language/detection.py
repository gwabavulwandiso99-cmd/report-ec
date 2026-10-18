"""Guesses whether a message is English, isiXhosa or a mix of both.

The guess is based on counting evidence:
- common English / isiXhosa words (language_markers in lexicon.json)
- matched municipal vocabulary (each match knows its language)
- a weak isiXhosa hint for unknown words that end in a vowel and contain
  letter groups typical of isiXhosa (hl, dl, tsh, ngx, xh, qh, gq ...)

It is a heuristic for short complaints, not a general language identifier.
The chatbot never switches language on its own; the resident's chosen
language is always used for replies.
"""
from dataclasses import dataclass, field

from report_ec_ai.config import lexicon
from report_ec_ai.language.vocabulary import complaint_vocabulary

VOWELS = set("aeiou")


@dataclass
class LanguageGuess:
    language: str          # "en", "xh", "mixed" or "unknown"
    confidence: float      # 0.0 - 1.0, see docs/ARCHITECTURE.md
    english_evidence: float = 0.0
    xhosa_evidence: float = 0.0
    notes: list = field(default_factory=list)


def _looks_xhosa(token: str, clusters: list) -> bool:
    return (
        len(token) >= 5
        and token[-1] in VOWELS
        and any(c in token for c in clusters)
    )


def detect_language(tokens: list, matches: list = None) -> LanguageGuess:
    data = lexicon()
    en_markers = set(data["language_markers"]["en"])
    xh_markers = set(data["language_markers"]["xh"])
    clusters = data["xh_clusters"]
    if matches is None:
        matches = complaint_vocabulary().match(tokens)

    english = 0.0
    xhosa = 0.0
    matched_positions = {}
    for m in matches:
        matched_positions[m.position] = m

    for i, token in enumerate(tokens):
        if token.isdigit():
            continue
        if i in matched_positions:
            lang = matched_positions[i].language
            if lang == "en":
                english += 1
            elif lang == "xh":
                xhosa += 1
            else:  # English word inside an isiXhosa prefix = both
                english += 0.5
                xhosa += 0.5
            continue
        if token in xh_markers:
            xhosa += 1
        elif token in en_markers:
            english += 1
        elif _looks_xhosa(token, clusters):
            xhosa += 0.5

    total = english + xhosa
    if total == 0:
        return LanguageGuess("unknown", 0.0, 0, 0, ["No known English or isiXhosa words found."])

    # More evidence = more confidence; three or more clues is treated as enough
    amount = min(1.0, total / 3.0)
    smaller = min(english, xhosa)
    if smaller >= 1 and smaller / total >= 0.15:
        language = "mixed"
        confidence = round(amount * (1 - abs(english - xhosa) / total * 0.5), 2)
    elif english >= xhosa:
        language = "en"
        confidence = round(amount * english / total, 2)
    else:
        language = "xh"
        confidence = round(amount * xhosa / total, 2)
    return LanguageGuess(language, confidence, english, xhosa)
