"""Priority suggestions (Module C; required test 16 and context rules)."""
import pytest

from report_ec_ai.intelligence.analyzer import analyze_complaint
from report_ec_ai.intelligence.prioritization import find_duration_days


def priority(text):
    return analyze_complaint(text).priority


# 16. Safety-related priority suggestion
@pytest.mark.parametrize("text", [
    "Exposed electrical cable near the school",
    "Live wires lying on the ground where children play",
    "Iintambo zombane ezixhomekileyo kufuphi nesikolo",
    "The transformer is sparking and smoking near the houses",
    "Open manhole near the creche",
])
def test_life_threatening_hazards_are_critical(text):
    result = priority(text)
    assert result.priority == "critical"
    assert result.safety_indicators
    assert analyze_complaint(text).needs_human_review is True
    assert any("critical" in r.lower() for r in result.reasons)


def test_same_category_different_context_gives_different_priority():
    # Category keyword alone does not decide the level
    assert priority("Power outage in our street").priority == "medium"
    assert priority("Exposed electrical cable near the school").priority == "critical"
    assert priority("Streetlight broken").priority == "low"


@pytest.mark.parametrize("text, expected", [
    ("Major sewage overflow near our homes", "high"),
    ("Dangerous pothole on a busy road", "high"),
    ("Pothole in the road", "medium"),
    ("Please repaint the faded road sign when possible", "low"),
    ("No water in the whole area", "high"),
    ("No water since this morning", "medium"),
    ("Akukho manzi kwiintsuku ezintathu", "high"),
    ("Rubbish not collected for two weeks", "medium"),
    ("Rubbish bin not collected", "low"),
])
def test_context_rules(text, expected):
    assert priority(text).priority == expected


def test_urgent_word_alone_cannot_make_critical():
    result = priority("URGENT URGENT emergency please help streetlight broken")
    assert result.priority in ("low", "medium")
    assert result.priority != "critical"


def test_negated_danger_is_not_counted():
    assert priority("Big pothole, not dangerous").priority == "medium"
    assert priority("Dangerous pothole").priority == "high"


def test_unknown_facts_are_not_invented():
    result = priority("Something is broken")
    assert result.priority is None
    assert "Not enough information" in result.explanation
    assert result.duration_days is None


def test_duration_parsing():
    assert find_duration_days("no water for three days") == 3
    assert find_duration_days("not collected for 2 weeks") == 14
    assert find_duration_days("akukho manzi kwiintsuku ezintathu") == 3
    assert find_duration_days("iiveki ezimbini") == 14
    assert find_duration_days("iintsuku ezi-4") == 4
    assert find_duration_days("since last week") == 7
    assert find_duration_days("since this morning") is None


def test_low_priority_maps_to_medium_for_the_current_dashboard():
    result = priority("Streetlight broken")
    assert result.priority == "low"
    assert result.legacy_urgency == "medium"


def test_location_context_can_raise_urgency_but_not_change_category():
    without = analyze_complaint("Hanging electric wires on the pole")
    with_school = analyze_complaint("Hanging electric wires on the pole", location_text="Zwide, next to the school")
    assert without.priority.priority == "high"
    assert with_school.priority.priority == "critical"
    assert with_school.category.category == "electricity"
    assert analyze_complaint("Big pothole", location_text="near the clinic").category.category == "roads"
