"""Category suggestions (Module C; required tests 13, 14, 15, 17)."""
import pytest

from report_ec_ai.intelligence.analyzer import analyze_complaint


# 13. English category prediction
@pytest.mark.parametrize("text, category, subcategory", [
    ("There is a burst water pipe near the clinic.", "water", "burst_or_leak"),
    ("No water in KwaZakhele for three days", "water", "no_supply"),
    ("Power outage in our street since last night", "electricity", "outage"),
    ("Streetlight broken in our street", "electricity", "streetlight"),
    ("Exposed electrical cable hanging near the school", "electricity", "exposed_hazard"),
    ("Dangerous pothole on the main road", "roads", "pothole"),
    ("Sewage overflowing near our homes", "sanitation", "sewage_overflow"),
    ("Rubbish not collected for two weeks", "waste", "uncollected"),
    ("People are dumping rubbish behind the houses", "waste", "illegal_dumping"),
    ("The playground swings are broken", "public_infrastructure", "general"),
])
def test_english_categories(text, category, subcategory):
    result = analyze_complaint(text)
    assert result.category.category == category
    assert result.category.subcategory == subcategory
    assert result.needs_clarification is False


# 14. isiXhosa category prediction
@pytest.mark.parametrize("text, category", [
    ("Kukho umbhobho wamanzi ogqabhukileyo kufuphi nekliniki.", "water"),
    ("Akukho manzi kwiintsuku ezintathu", "water"),
    ("Akukho mbane eKwaZakhele", "electricity"),
    ("Iintambo zombane ezixhomekileyo kufuphi nesikolo", "electricity"),
    ("Kukho umngxuma omkhulu endleleni", "roads"),
    ("Ilindle liyaphuphuma emakhayeni", "sanitation"),
    ("Inkunkuma ayithathwanga iiveki ezimbini", "waste"),
])
def test_isixhosa_categories(text, category):
    result = analyze_complaint(text)
    assert result.category.category == category
    assert result.language.detected == "xh"


# 15. Code-switched category prediction
@pytest.mark.parametrize("text, category", [
    ("Kukho water leak kufuphi neclinic.", "water"),
    ("Akukho electricity since yesterday eZwide", "electricity"),
    ("Kukho pothole enkulu on the main road", "roads"),
    ("Sewage iyaphuphuma near the creche", "sanitation"),
])
def test_code_switched_categories(text, category):
    result = analyze_complaint(text)
    assert result.category.category == category
    assert result.language.detected == "mixed"


# 17. Unclear complaint handling
@pytest.mark.parametrize("text", ["hello", "My neighbour is noisy", "Please come and check", "", "?????"])
def test_unclear_complaints_ask_for_clarification(text):
    result = analyze_complaint(text)
    assert result.needs_clarification is True
    assert result.category.category == "other"
    assert result.category.confidence == "low"
    assert result.priority.priority is None          # no invented urgency
    assert result.needs_human_review is True


def test_tie_between_categories_is_low_confidence():
    # water and roads equally likely -> do not pretend to know
    result = analyze_complaint("water road")
    assert result.category.confidence == "low"
    assert result.needs_clarification is True


def test_original_text_is_preserved_in_analysis():
    text = "Kukho water leak kufuphi neclinic!!"
    assert analyze_complaint(text).original_text == text


def test_output_is_marked_as_advice():
    result = analyze_complaint("Burst pipe near the clinic")
    assert result.advisory_only is True
    assert "not a municipal decision" in result.advisory_notice.lower()
    assert result.model_version


def test_legacy_mapping_for_existing_dashboard():
    assert analyze_complaint("Burst pipe near the clinic").category.legacy_category == "water_leak"
    assert analyze_complaint("No water since yesterday").category.legacy_category == "no_water"
    assert analyze_complaint("Power outage in Zwide").category.legacy_category == "power_outage"
    assert analyze_complaint("Big pothole in the road").category.legacy_category == "pothole"
    assert analyze_complaint("Streetlight not working").category.legacy_category == "streetlight"


def test_resident_choice_overrides_suggestion():
    result = analyze_complaint("Something is wrong", forced_category="waste")
    assert result.category.category == "waste"
    assert result.category.chosen_by_resident is True
    assert result.needs_clarification is False
    with pytest.raises(ValueError):
        analyze_complaint("x", forced_category="aliens")
