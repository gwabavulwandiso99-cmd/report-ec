"""Runs the whole complaint pipeline in one call:

    original text -> normalise -> match vocabulary -> detect language
                  -> suggest category -> suggest priority -> location hints
                  -> decide if clarification / human review is needed

The original text is copied into the result unchanged.
"""
from typing import Optional

from report_ec_ai.config import taxonomy
from report_ec_ai.integration.schemas import (
    CategoryResult,
    ComplaintAnalysis,
    LanguageInfo,
    LocationHintsResult,
    MatchedTerm,
    PriorityResult,
)
from report_ec_ai.intelligence.categorization import suggest_category
from report_ec_ai.intelligence.guards import looks_like_instruction
from report_ec_ai.intelligence.prioritization import SAFETY_LABELS, assess_priority
from report_ec_ai.language.detection import detect_language
from report_ec_ai.language.locations import extract_location
from report_ec_ai.language.normalization import normalize
from report_ec_ai.language.vocabulary import complaint_vocabulary


class ComplaintAnalyzer:
    """Rule-based complaint intelligence. No external services, no paid APIs.

    To try a machine-learning or LLM model later, subclass this and override
    `analyze`, but keep returning a `ComplaintAnalysis` so nothing else changes.
    """

    def analyze(self, text: str, language_hint: Optional[str] = None,
                forced_category: Optional[str] = None,
                forced_subcategory: Optional[str] = None,
                location_text: Optional[str] = None) -> ComplaintAnalysis:
        """`location_text` (optional) is what the resident typed as the location. Only
        place context from it (school, clinic, busy road, homes) is used, for the
        urgency; it never changes the category."""
        if forced_category and forced_category not in taxonomy()["categories"]:
            raise ValueError(f"Unknown category: {forced_category}")
        if forced_subcategory and forced_category and \
                forced_subcategory not in taxonomy()["categories"][forced_category]["subcategories"]:
            raise ValueError(f"Unknown subcategory: {forced_subcategory}")

        vocab = complaint_vocabulary()
        normalized = normalize(text)
        matches = vocab.match(normalized.tokens)
        flags = vocab.flags_for(matches)
        concept_ids = {m.concept_id for m in matches}
        language = detect_language(normalized.tokens, matches)
        context_flags = set()
        if location_text:
            for m in vocab.match(normalize(location_text).tokens):
                concept = vocab.concepts[m.concept_id]
                if concept.get("kind") in ("landmark", "context"):
                    context_flags.update(concept.get("flags", []))
        context_flags -= flags

        category = suggest_category(matches, forced_category, forced_subcategory)
        priority = assess_priority(
            category.category, category.subcategory, concept_ids, flags | context_flags,
            normalized.normalized, category.confidence,
        )
        if context_flags:
            priority.reasons.insert(1, "Place context taken from the location the resident gave: "
                                       + ", ".join(sorted(context_flags)) + ".")
        location = extract_location(text, normalized, matches)
        instruction_like = looks_like_instruction(normalized.original)

        # When should the chatbot ask the resident to clarify?
        clarification_reason = None
        if not normalized.tokens:
            clarification_reason = "empty"
        elif category.category == "other" and not category.forced_by_resident:
            clarification_reason = "category_unknown"
        elif category.confidence == "low":
            clarification_reason = "category_uncertain"

        # When should staff check the AI suggestion before relying on it?
        review = []
        if priority.priority in ("critical", "high"):
            review.append(f"Suggested urgency is {priority.priority}.")
        if priority.safety_indicators:
            review.append("Safety indicators found.")
        if priority.priority is None:
            review.append("No urgency could be suggested.")
        if category.confidence == "low" and not category.forced_by_resident:
            review.append("Category confidence is low.")
        if language.language == "unknown" or language.confidence < 0.4:
            review.append("Language could not be identified with confidence.")
        if instruction_like:
            review.append("Text looks like an instruction to the system; it was treated as plain text.")

        return ComplaintAnalysis(
            original_text=normalized.original,
            normalized_text=normalized.normalized,
            corrections=[list(c) for c in normalized.corrections],
            language=LanguageInfo(
                detected=language.language,
                confidence=language.confidence,
                english_evidence=language.english_evidence,
                xhosa_evidence=language.xhosa_evidence,
            ),
            matched_terms=[
                MatchedTerm(concept=m.concept_id, text=m.text, language=m.language, method=m.method)
                for m in matches
            ],
            category=CategoryResult(
                category=category.category,
                subcategory=category.subcategory,
                confidence=category.confidence,
                evidence_score=category.evidence_score,
                explanation=category.explanation,
                scores=category.scores,
                legacy_category=category.legacy_category,
                chosen_by_resident=category.forced_by_resident,
            ),
            priority=PriorityResult(
                priority=priority.priority,
                confidence=priority.confidence,
                explanation=priority.explanation,
                reasons=priority.reasons,
                safety_indicators=priority.safety_indicators,
                safety_descriptions=[SAFETY_LABELS[s] for s in priority.safety_indicators],
                duration_days=priority.duration_days,
                legacy_urgency=priority.legacy_urgency,
            ),
            location_hints=LocationHintsResult(
                area=location.area,
                municipality=location.municipality,
                landmark=location.landmark,
                landmark_words=location.landmark_words,
                ward=location.ward,
            ),
            needs_clarification=clarification_reason is not None,
            clarification_reason=clarification_reason,
            needs_human_review=bool(review),
            review_reasons=review,
            instruction_like_text=instruction_like,
        )


_default = ComplaintAnalyzer()


def analyze_complaint(text: str, language_hint: Optional[str] = None, **kwargs) -> ComplaintAnalysis:
    return _default.analyze(text, language_hint, **kwargs)
