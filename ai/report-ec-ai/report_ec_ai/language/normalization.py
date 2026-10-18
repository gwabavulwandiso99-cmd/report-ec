"""Text clean-up before matching.

The resident's original words are NEVER changed. `normalize()` returns a new
object that holds the original text next to a cleaned copy used for matching,
plus a list of every spelling correction that was applied.
"""
import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from report_ec_ai.config import lexicon

# Control characters (except new line and tab) are removed from input
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# A hyphen between letters is dropped, e.g. "i-clinic" -> "iclinic", "e-Zwide" -> "ezwide"
_HYPHEN_IN_WORD = re.compile(r"(?<=[a-z])-(?=[a-z])")
_TOKEN = re.compile(r"[a-z0-9']+")
# Three or more of the same letter become two ("pleeease" -> "please")
_REPEATED = re.compile(r"([a-z])\1{2,}")


@dataclass
class NormalizedText:
    original: str
    normalized: str
    tokens: list
    corrections: list = field(default_factory=list)  # (from_word, to_word, method)


def clean_input(text) -> str:
    """Make any incoming text safe to work with. Used for every resident message."""
    if text is None:
        return ""
    text = str(text)
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_CHARS.sub("", text)
    return text.strip()


def _known_english_words() -> list:
    """Single English words from the lexicon, used for fuzzy spelling correction."""
    words = set()
    for concept in lexicon()["complaint_concepts"]:
        for term in concept.get("en", []):
            if " " not in term and len(term) >= 5:
                words.add(term)
    return sorted(words)


_ENGLISH_WORDS = None


def normalize(text, fuzzy: bool = True) -> NormalizedText:
    global _ENGLISH_WORDS
    original = clean_input(text)
    lowered = original.lower()
    # Curly quotes and accents (crèche -> creche)
    lowered = lowered.replace("’", "'").replace("‘", "'")
    lowered = "".join(
        ch for ch in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(ch)
    )
    lowered = _HYPHEN_IN_WORD.sub("", lowered)
    lowered = _REPEATED.sub(r"\1\1", lowered)

    spelling = lexicon()["spelling"]
    markers = set(lexicon()["language_markers"]["en"]) | set(lexicon()["language_markers"]["xh"])
    if _ENGLISH_WORDS is None:
        _ENGLISH_WORDS = _known_english_words()

    tokens = []
    corrections = []
    for token in _TOKEN.findall(lowered):
        token = token.strip("'")
        if not token:
            continue
        if token in spelling and not token.startswith("_"):
            fixed = spelling[token]
            if fixed != token:
                corrections.append((token, fixed, "spelling_list"))
            tokens.extend(fixed.split())
            continue
        # Fuzzy correction only for longer words that are not already known words,
        # and only against English vocabulary (isiXhosa words are left alone because
        # small spelling changes can change their meaning).
        if fuzzy and len(token) >= 6 and token not in markers and not token.isdigit():
            if token not in _ENGLISH_WORDS:
                close = difflib.get_close_matches(token, _ENGLISH_WORDS, n=1, cutoff=0.88)
                if close:
                    corrections.append((token, close[0], "fuzzy"))
                    tokens.append(close[0])
                    continue
        tokens.append(token)

    return NormalizedText(
        original=original,
        normalized=" ".join(tokens),
        tokens=tokens,
        corrections=corrections,
    )
