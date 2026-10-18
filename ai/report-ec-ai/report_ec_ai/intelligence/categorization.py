"""Suggests a category (and subcategory) for a complaint.

Each matched word adds its weight to a category (weights are in lexicon.json).
The category with the highest total is suggested. Confidence depends on how
much evidence there is and how far ahead the winner is:

    high    top score >= 2.0 and at least twice the second-best score
    medium  top score >= 1.0 and clearly ahead of the second-best score
    low     anything else (no evidence, weak evidence or a tie)

`evidence_score` (0-1) is top / (top + second + 1). It shows how one-sided
the evidence is. It is NOT a probability and has not been calibrated on real
reports.
"""
from dataclasses import dataclass, field
from typing import Optional

from report_ec_ai.config import taxonomy
from report_ec_ai.language.vocabulary import complaint_vocabulary

PHRASE_BONUS = 0.5


@dataclass
class CategorySuggestion:
    category: str
    subcategory: str
    confidence: str                 # "high", "medium", "low"
    evidence_score: float
    scores: dict
    matched_terms: list = field(default_factory=list)   # [(term, category)]
    explanation: str = ""
    legacy_category: str = "other"
    forced_by_resident: bool = False


def category_scores(matches: list) -> dict:
    vocab = complaint_vocabulary()
    scores = {}
    for m in matches:
        concept = vocab.concepts[m.concept_id]
        category = concept.get("category")
        if not category:
            continue
        weight = concept.get("weight", 1.0)
        if m.method == "phrase":
            weight += PHRASE_BONUS
        scores[category] = round(scores.get(category, 0.0) + weight, 2)
    return scores


def choose_subcategory(category: str, concept_ids: set) -> str:
    subcategories = taxonomy()["categories"][category]["subcategories"]
    for name, rule in subcategories.items():
        if rule["requires_any"] and concept_ids & set(rule["requires_any"]):
            return name
    return "general"


def has_electrical_hazard(concept_ids: set, flags: set) -> bool:
    """Exposed/hanging/fallen cable, or a cable with danger words, counts as a hazard."""
    if "electrical_hazard" in flags:
        return True
    if "cable" in concept_ids and ({"exposed", "danger", "fire"} & flags):
        return True
    return False


def suggest_category(matches: list, forced_category: Optional[str] = None,
                     forced_subcategory: Optional[str] = None) -> CategorySuggestion:
    vocab = complaint_vocabulary()
    scores = category_scores(matches)
    concept_ids = {m.concept_id for m in matches}
    flags = vocab.flags_for(matches)
    # "cable" + "hanging" together is strong evidence for electricity, stronger than each word alone
    if has_electrical_hazard(concept_ids, flags) and "electrical_hazard" not in flags:
        scores["electricity"] = round(scores.get("electricity", 0.0) + 1.0, 2)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    matched_terms = [
        (m.text, vocab.concepts[m.concept_id].get("category"))
        for m in matches if vocab.concepts[m.concept_id].get("category")
    ]

    if forced_category:
        # The resident chose the category themselves (clarification menu)
        category = forced_category
        subcategory = forced_subcategory or choose_subcategory(category, concept_ids)
        if category == "electricity" and has_electrical_hazard(concept_ids, flags) and not forced_subcategory:
            subcategory = "exposed_hazard"
        legacy = taxonomy()["categories"][category]["subcategories"][subcategory]["legacy_category"]
        return CategorySuggestion(
            category, subcategory, "high", 1.0, scores, matched_terms,
            "Category chosen by the resident.", legacy, forced_by_resident=True,
        )

    if not ranked:
        return CategorySuggestion(
            "other", "general", "low", 0.0, scores, [],
            "No municipal-service words were recognised, so the category is unknown.",
            "other",
        )

    top_category, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    evidence = round(top / (top + second + 1.0), 2)
    if top >= 2.0 and second <= top / 2:
        confidence = "high"
    elif top >= 1.0 and top > second * 1.25:
        confidence = "medium"
    else:
        confidence = "low"

    subcategory = choose_subcategory(top_category, concept_ids)
    if top_category == "electricity" and has_electrical_hazard(concept_ids, flags):
        subcategory = "exposed_hazard"
    legacy = taxonomy()["categories"][top_category]["subcategories"][subcategory]["legacy_category"]

    words = [term for term, cat in matched_terms if cat == top_category]
    explanation = f"Words linked to '{top_category}': {', '.join(words)} (score {top})."
    if len(ranked) > 1:
        explanation += f" Next best: '{ranked[1][0]}' (score {second})."
    if confidence == "low":
        explanation += " The evidence is weak, so the resident should be asked to confirm."

    return CategorySuggestion(
        top_category, subcategory, confidence, evidence, scores, matched_terms, explanation, legacy,
    )
