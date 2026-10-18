"""Pulls location hints (area, landmark, ward, municipality) out of a message.

Hints only: anything the resident types is still kept word for word in the
report, even when no area or landmark is recognised.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from report_ec_ai.config import places
from report_ec_ai.language.normalization import normalize
from report_ec_ai.language.vocabulary import complaint_vocabulary

_WARD_PATTERNS = [
    re.compile(r"\bward\s*(?:no\.?|number|#)?\s*(\d{1,3})\b"),
    re.compile(r"\b(?:i|e)?wadi\s*(?:yesi|yesi-|ye|ka|engu)?\s*-?\s*(\d{1,3})\b"),
]

# Words people use when they do not know the location
DONT_KNOW = {
    "i don't know", "i dont know", "dont know", "don't know", "not sure", "no idea",
    "unknown", "idk", "andazi", "andiyazi", "andiqinisekanga", "?", "??",
}


@dataclass
class LocationHints:
    area: Optional[str] = None
    municipality: Optional[str] = None
    landmark: Optional[str] = None           # canonical id, e.g. "clinic"
    landmark_words: Optional[str] = None     # how the resident wrote it, e.g. "nekliniki"
    ward: Optional[str] = None
    all_landmarks: list = field(default_factory=list)

    @property
    def has_place(self) -> bool:
        return bool(self.area or self.landmark)


def _area_index():
    """(alias tokens, area name, municipality) sorted longest first."""
    index = []
    for municipality, info in places()["municipalities"].items():
        for area, aliases in info["areas"].items():
            for alias in aliases:
                index.append((tuple(alias.split()), area, municipality))
    index.sort(key=lambda item: len(item[0]), reverse=True)
    return index


def find_area(tokens: list):
    for alias, area, municipality in _area_index():
        size = len(alias)
        for start in range(len(tokens) - size + 1):
            window = tokens[start:start + size]
            if tuple(window) == alias:
                return area, municipality
            # isiXhosa locative or prefix on the first word: "ezwide", "kwizwide", "emotherwell"
            first = window[0]
            for prefix in ("e", "kwi", "ku", "ka", "se"):
                if first == prefix + alias[0] and tuple(window[1:]) == alias[1:]:
                    return area, municipality
    return None, None


def find_municipality(tokens: list) -> Optional[str]:
    text = " " + " ".join(tokens) + " "
    for municipality, info in places()["municipalities"].items():
        for alias in info["aliases"]:
            if f" {alias} " in text:
                return municipality
    return None


def find_ward(normalized_text: str, max_ward: int = None) -> Optional[str]:
    max_ward = max_ward or places()["max_ward"]
    for pattern in _WARD_PATTERNS:
        found = pattern.search(normalized_text)
        if found:
            number = int(found.group(1))
            if 1 <= number <= max_ward:
                return str(number)
    return None


def extract_location(text: str, normalized=None, matches=None) -> LocationHints:
    normalized = normalized or normalize(text)
    if matches is None:
        matches = complaint_vocabulary().match(normalized.tokens)
    vocab = complaint_vocabulary()
    hints = LocationHints()
    hints.area, hints.municipality = find_area(normalized.tokens)
    if hints.municipality is None:
        hints.municipality = find_municipality(normalized.tokens)
    hints.ward = find_ward(normalized.normalized)
    for m in matches:
        concept = vocab.concepts[m.concept_id]
        if concept.get("kind") == "landmark":
            hints.all_landmarks.append(concept["landmark"])
            if hints.landmark is None:
                hints.landmark = concept["landmark"]
                hints.landmark_words = m.text
    return hints


def is_dont_know(text: str) -> bool:
    cleaned = normalize(text, fuzzy=False).normalized
    return cleaned in DONT_KNOW or text.strip() in DONT_KNOW
