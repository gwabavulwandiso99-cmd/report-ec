"""Suggests how urgent a complaint is: critical, high, medium or low.

The level is built up from the complaint's meaning, not from the category alone:

1. Start from the subcategory's base level (config/taxonomy.json).
2. Lower it one step for routine requests ("when possible", "repaint", "slow drip").
3. Raise it for context found in the complaint:
   - physical danger (exposed wires, fire, open manhole, injury)
   - a vulnerable place or people nearby (school, clinic, children, elderly)
   - sewage or flooding reaching homes
   - a busy road or accidents (roads)
   - many people affected, or a long duration (for supply problems)
4. "critical" is ONLY used when there is a sign of immediate danger to life
   (electrical hazard, fire, open manhole or injury) together with a
   vulnerable place/people, fire or injury. Words like "urgent" alone can
   never make a report critical.

If there is not enough information (category unknown and no danger signs),
no level is suggested at all (`priority = None`) instead of guessing.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from report_ec_ai.config import taxonomy
from report_ec_ai.intelligence.categorization import has_electrical_hazard

LEVELS = ["low", "medium", "high", "critical"]

NUMBER_WORDS = {
    "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "a few": 3, "few": 3, "several": 3,
    # isiXhosa (class 10 agreement used with iintsuku / iiveki)
    "ezimbini": 2, "ezintathu": 3, "ezine": 4, "ezintlanu": 5, "ezithandathu": 6,
    "ezisixhenxe": 7,
}
_EN_DURATION = re.compile(
    r"\b(\d+|a few|few|several|a|one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|days|week|weeks|month|months)\b"
)
_XH_DURATION = re.compile(
    r"\b(?:k?wi?intsuku|iintsuku|intsuku|iiveki|iveki|iinyanga|inyanga)\s+(?:ezi-?\s*(?=\d))?(\d+|\w+)\b"
)
_UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30}

SAFETY_LABELS = {
    "electrical_hazard": "Exposed or damaged electrical cables (risk of electrocution)",
    "fire": "Fire, sparks or smoke mentioned",
    "open_manhole": "Open or uncovered manhole (risk of falling in)",
    "injury": "Someone hurt or an accident mentioned",
    "flooding": "Flooding mentioned",
    "sewage_near_people": "Sewage near homes or a vulnerable place (health risk)",
    "danger": "Resident describes it as dangerous",
    "traffic_risk": "Road problem on a busy road or with accidents",
}


@dataclass
class PrioritySuggestion:
    priority: Optional[str]
    confidence: str
    reasons: list = field(default_factory=list)
    safety_indicators: list = field(default_factory=list)   # keys of SAFETY_LABELS
    duration_days: Optional[int] = None
    legacy_urgency: Optional[str] = None

    @property
    def explanation(self) -> str:
        return " ".join(self.reasons)


def find_duration_days(normalized_text: str) -> Optional[int]:
    """Longest duration mentioned, in days. Only what the resident actually wrote."""
    longest = None
    for number, unit in _EN_DURATION.findall(normalized_text):
        amount = int(number) if number.isdigit() else NUMBER_WORDS.get(number)
        if amount:
            days = amount * _UNIT_DAYS[unit]
            longest = max(longest or 0, days)
    for match in _XH_DURATION.finditer(normalized_text):
        word = match.group(0).split()[0]
        number = match.group(1)
        amount = int(number) if number.isdigit() else NUMBER_WORDS.get(number)
        if amount is None:
            continue
        unit_days = 7 if "veki" in word else 30 if "nyanga" in word else 1
        longest = max(longest or 0, amount * unit_days)
    if longest is None and re.search(r"\b(?:last week|since last week|kwiveki ephelileyo|ngeveki ephelileyo)\b", normalized_text):
        longest = 7
    return longest


def _raise(level: int, to: int) -> int:
    return max(level, to)


def assess_priority(category: str, subcategory: str, concept_ids: set, flags: set,
                    normalized_text: str, category_confidence: str) -> PrioritySuggestion:
    rule = taxonomy()["categories"][category]["subcategories"][subcategory]
    base = rule["base_priority"]
    reasons = []
    safety = []

    # Danger words that are negated ("not dangerous", "akukho ngozi") are ignored
    if "no_danger" in flags:
        flags = flags - {"danger"}
        reasons.append("Resident says there is no danger.")

    electrical = has_electrical_hazard(concept_ids, flags)
    vulnerable = bool({"vulnerable_place", "vulnerable_people"} & flags)
    life_risk = electrical or bool({"fire", "open_manhole", "injury"} & flags)

    if base is None and not life_risk and "danger" not in flags:
        return PrioritySuggestion(
            None, "low",
            ["Not enough information to suggest an urgency. Staff should assess it."],
            [], find_duration_days(normalized_text), None,
        )

    level = LEVELS.index(base) if base else LEVELS.index("medium")
    reasons.append(f"Starting level for {category}/{subcategory}: {LEVELS[level]}.")

    if "routine" in flags and not life_risk and "danger" not in flags:
        if level > 0:
            level -= 1
            reasons.append("Described as routine or minor, so lowered one level.")

    # --- Immediate physical danger ---
    if electrical:
        safety.append("electrical_hazard")
        level = _raise(level, 2)
        reasons.append("Exposed or damaged electrical cables can cause electrocution.")
    if "fire" in flags:
        safety.append("fire")
        level = _raise(level, 2)
        reasons.append("Fire, sparks or smoke are mentioned.")
    if "open_manhole" in flags:
        safety.append("open_manhole")
        level = _raise(level, 2)
        reasons.append("An open manhole is a falling hazard.")
    if "injury" in flags:
        safety.append("injury")
        level = _raise(level, 2)
        reasons.append("Someone was hurt or an accident is mentioned.")

    # Critical = immediate danger to life AND something that makes harm likely
    critical_reason = None
    if electrical and (vulnerable or {"injury", "fire"} & flags):
        critical_reason = "Electrical hazard near a school, clinic or vulnerable people, or with injury/fire."
    elif "open_manhole" in flags and vulnerable:
        critical_reason = "Open manhole where children or other vulnerable people are."
    elif "fire" in flags and (category == "electricity" or "near_homes" in flags or vulnerable):
        critical_reason = "Fire involving electrical equipment, homes or a vulnerable place."
    if critical_reason:
        level = 3
        reasons.append(f"Suggested as critical: {critical_reason}")

    # --- Health and wider impact (can raise to high, never to critical) ---
    if category == "sanitation" and ({"sewage", "overflow", "toilet"} & flags) and ("near_homes" in flags or vulnerable):
        safety.append("sewage_near_people")
        level = _raise(level, 2)
        reasons.append("Sewage near homes or a vulnerable place is a health risk.")
    if "flooding" in flags:
        safety.append("flooding")
        if "near_homes" in flags or vulnerable:
            level = _raise(level, 2)
            reasons.append("Flooding is reaching homes or a vulnerable place.")
        elif level < 2:
            level += 1
            reasons.append("Flooding is mentioned.")
    if category == "roads" and ({"busy_road", "injury"} & flags or "danger" in flags):
        safety.append("traffic_risk")
        level = _raise(level, 2)
        reasons.append("Road problem on a busy road or with accidents/danger.")
    if "danger" in flags and level < 2:
        safety.append("danger")
        level += 1
        reasons.append("Resident describes it as dangerous.")
    if vulnerable and level < 2 and not life_risk:
        level += 1
        reasons.append("A school, clinic or vulnerable people are affected.")
    if "wide_impact" in flags and level < 2:
        level += 1
        reasons.append("Many people or a whole area are affected.")

    duration = find_duration_days(normalized_text)
    if duration is not None:
        if subcategory in ("no_supply", "outage") and duration >= 2 and level < 2:
            level += 1
            reasons.append(f"Service has been out for about {duration} day(s).")
        elif category == "waste" and duration >= 7 and level < 2:
            level += 1
            reasons.append(f"Waste has not been collected for about {duration} day(s).")
        else:
            reasons.append(f"Duration mentioned: about {duration} day(s).")

    if "resident_urgent" in flags:
        if level == 0:
            level = 1
            reasons.append("Resident says it is urgent, so raised from low to medium.")
        else:
            reasons.append("Resident says it is urgent (noted; urgency words alone do not raise the level further).")

    # Confidence: explicit danger words make the suggestion firmer; a weak category makes it weaker
    if category_confidence == "low":
        confidence = "low"
    elif safety or "routine" in flags:
        confidence = "high" if category_confidence == "high" else "medium"
    else:
        confidence = "medium"

    priority = LEVELS[level]
    legacy = taxonomy()["legacy_urgency"][priority]
    # Keep the list in a stable order without repeats
    safety = list(dict.fromkeys(safety))
    return PrioritySuggestion(priority, confidence, reasons, safety, duration, legacy)
