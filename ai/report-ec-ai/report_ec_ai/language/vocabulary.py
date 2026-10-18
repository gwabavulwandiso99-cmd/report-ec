"""Finds municipal-service words in a complaint (English, isiXhosa or a mix).

How matching works, in order:
1. Phrases with more than one word ("no water", "akukho mbane") are matched first.
   Words used by a phrase are not used again, so "no water" is not also counted
   as plain "water".
2. Whole single words from the English and isiXhosa lists.
3. isiXhosa stems: common prefixes are removed and the rest is compared with the
   stem list, e.g. "wamanzi" -> "amanzi" -> "manzi" (water).
4. English words written with an isiXhosa prefix (code-switching), e.g.
   "neclinic" -> "clinic", "ewater" -> "water".

This is a vocabulary matcher for one domain. It does not understand isiXhosa
grammar and it can miss or mis-read words; the chatbot asks the resident to
clarify when the evidence is weak.
"""
from dataclasses import dataclass
from functools import lru_cache

from report_ec_ai.config import lexicon


@dataclass(frozen=True)
class ConceptMatch:
    concept_id: str
    text: str          # the words from the complaint that matched
    language: str      # "en", "xh" or "mixed" (English word with isiXhosa prefix)
    method: str        # "phrase", "word", "stem" or "prefixed_loanword"
    position: int      # index of the first token


class Vocabulary:
    def __init__(self, concepts: list, prefixes: list, english_markers: list):
        self.concepts = {c["id"]: c for c in concepts}
        self.prefixes = sorted(prefixes, key=len, reverse=True)
        self.english_markers = set(english_markers)
        self.phrases = []          # (tokens tuple, concept_id, language)
        self.words = {}            # word -> (concept_id, language)
        self.stems = []            # (stem, concept_id)
        for concept in concepts:
            for language in ("en", "xh"):
                for term in concept.get(language, []):
                    parts = tuple(term.lower().split())
                    if len(parts) > 1:
                        self.phrases.append((parts, concept["id"], language))
                    else:
                        # First concept to claim a word keeps it (the list order is deliberate)
                        self.words.setdefault(parts[0], (concept["id"], language))
            for stem in concept.get("xh_stems", []):
                self.stems.append((stem.lower(), concept["id"]))
        # Longest phrases first, so "hole in the road" wins over "road"
        self.phrases.sort(key=lambda p: len(p[0]), reverse=True)
        self.english_words = {w for w, (_, lang) in self.words.items() if lang == "en"}

    def strip_prefixes(self, token: str, depth: int = 2) -> set:
        """All the forms of a word with up to `depth` isiXhosa prefixes removed."""
        forms = {token}
        current = {token}
        for _ in range(depth):
            next_forms = set()
            for form in current:
                for prefix in self.prefixes:
                    if form.startswith(prefix) and len(form) - len(prefix) >= 3:
                        next_forms.add(form[len(prefix):])
            next_forms -= forms
            forms |= next_forms
            current = next_forms
        return forms

    def match(self, tokens: list) -> list:
        used = [False] * len(tokens)
        matches = []

        # 1. Phrases. For isiXhosa phrases the first word may carry an extra
        #    prefix: "kwindlela enkulu" still matches "indlela enkulu".
        forms_cache = {}
        for parts, concept_id, language in self.phrases:
            size = len(parts)
            for start in range(len(tokens) - size + 1):
                if any(used[start:start + size]):
                    continue
                window = tuple(tokens[start:start + size])
                same = window == parts
                if not same and language == "xh" and window[1:] == parts[1:]:
                    first = window[0]
                    if first not in forms_cache:
                        forms_cache[first] = self.strip_prefixes(first)
                    # compare without the noun's own first vowel too ("indlela" -> "ndlela")
                    # (only the noun vowels i/u: dropping "a" from "akukho" would flip its meaning)
                    target = {parts[0]}
                    if parts[0][0] in "iu":
                        target.add(parts[0][1:])
                    same = bool(forms_cache[first] & target)
                if same:
                    matches.append(ConceptMatch(concept_id, " ".join(parts), language, "phrase", start))
                    for i in range(start, start + size):
                        used[i] = True

        # 2-4. Single words
        for i, token in enumerate(tokens):
            if used[i] or token.isdigit():
                continue
            if token in self.words:
                concept_id, language = self.words[token]
                matches.append(ConceptMatch(concept_id, token, language, "word", i))
                used[i] = True
                continue
            # English function words ("into", "near") are never treated as isiXhosa
            if token in self.english_markers:
                continue
            found = self._match_by_prefix(token)
            if found:
                concept_id, language, method = found
                matches.append(ConceptMatch(concept_id, token, language, method, i))
                used[i] = True

        matches.sort(key=lambda m: m.position)
        return matches

    def _match_by_prefix(self, token: str):
        forms = self.strip_prefixes(token)
        # isiXhosa word list or stems
        for form in sorted(forms, key=len, reverse=True):
            if form != token and form in self.words and self.words[form][1] == "xh":
                return self.words[form][0], "xh", "stem"
        for form in sorted(forms, key=len, reverse=True):
            for stem, concept_id in self.stems:
                if len(stem) >= 4 and form.startswith(stem):
                    return concept_id, "xh", "stem"
        # English loanword with an isiXhosa prefix: "neclinic", "ewater", "iwater"
        for form in sorted(forms, key=len, reverse=True):
            if form != token and len(form) >= 4 and form in self.english_words:
                return self.words[form][0], "mixed", "prefixed_loanword"
        return None

    def flags_for(self, matches: list) -> set:
        flags = set()
        for m in matches:
            flags.update(self.concepts[m.concept_id].get("flags", []))
        return flags


@lru_cache(maxsize=None)
def complaint_vocabulary() -> Vocabulary:
    data = lexicon()
    return Vocabulary(data["complaint_concepts"], data["xh_prefixes"], data["language_markers"]["en"])


@lru_cache(maxsize=None)
def verification_vocabulary() -> Vocabulary:
    data = lexicon()
    return Vocabulary(data["verification_cues"], data["xh_prefixes"], data["language_markers"]["en"])
