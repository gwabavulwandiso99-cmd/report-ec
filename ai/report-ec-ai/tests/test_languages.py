"""Language layer: normalisation, vocabulary, detection, prompts (Module B)."""
import json

import pytest

from report_ec_ai.chatbot.prompts import MissingPrompt, t
from report_ec_ai.config import RESOURCES_DIR
from report_ec_ai.language import complaint_vocabulary, detect_language, extract_location, normalize

ENGLISH = "There is a burst water pipe near the clinic."
XHOSA = "Kukho umbhobho wamanzi ogqabhukileyo kufuphi nekliniki."
MIXED = "Kukho water leak kufuphi neclinic."


def concepts(text):
    return {m.concept_id for m in complaint_vocabulary().match(normalize(text).tokens)}


def test_original_text_is_never_changed():
    text = "  Pleeease the WATTER is leeking!!  "
    result = normalize(text)
    assert result.original == text.strip()
    assert "water" in result.tokens and "leaking" in result.tokens
    assert ("watter", "water", "spelling_list") in result.corrections


def test_empty_and_strange_input():
    assert normalize(None).tokens == []
    assert normalize("").normalized == ""
    assert normalize("!!! ??? ...").tokens == []
    assert normalize("\x00\x01hello\x7f").original == "hello"
    assert normalize("crèche").tokens == ["creche"]
    assert normalize("i-clinic e-Zwide", fuzzy=False).tokens == ["iclinic", "ezwide"]
    assert "clinic" in concepts("i-clinic e-Zwide")


@pytest.mark.parametrize("text", [ENGLISH, XHOSA, MIXED])
def test_three_example_complaints_are_about_water_near_a_clinic(text):
    found = concepts(text)
    assert found & {"pipe", "leak", "water", "burst"}
    assert "clinic" in found


def test_isixhosa_prefixes_are_removed_for_matching():
    vocab = complaint_vocabulary()
    assert "manzi" in vocab.strip_prefixes("wamanzi")
    assert "kliniki" in vocab.strip_prefixes("nekliniki")
    assert "clinic" in vocab.strip_prefixes("neclinic")
    assert "vuza" in vocab.strip_prefixes("kusavuza")


def test_english_function_words_are_not_read_as_isixhosa():
    # "into" must not become "to", "near" must not become "ar" etc.
    assert concepts("Water is flowing into the street near us") == {"water", "roads"} or \
        "water" in concepts("Water is flowing into the street near us")
    assert not concepts("we are here about it")


def test_prefixed_isixhosa_phrase():
    assert "busy_road" in concepts("Irobhothi ayisebenzi kwindlela enkulu")


def test_negation_phrase_is_not_flipped():
    # "kukho manzi" (there is water) must not match "akukho manzi" (no water)
    assert "no_water" not in concepts("kukho manzi amaninzi")
    assert "no_water" in concepts("akukho manzi")


@pytest.mark.parametrize("text, expected", [
    (ENGLISH, "en"),
    (XHOSA, "xh"),
    (MIXED, "mixed"),
    ("Akukho mbane eKwaZakhele", "xh"),
    ("Please help, the pothole is very big", "en"),
])
def test_language_detection(text, expected):
    tokens = normalize(text).tokens
    assert detect_language(tokens).language == expected


def test_unknown_language():
    guess = detect_language(normalize("12345 !!!").tokens)
    assert guess.language == "unknown"
    assert guess.confidence == 0


def test_location_extraction():
    hints = extract_location("Burst pipe near the school in KwaZakhele, ward 14")
    assert hints.area == "KwaZakhele"
    assert hints.municipality == "Nelson Mandela Bay"
    assert hints.landmark == "school"
    assert hints.ward == "14"
    assert extract_location("eZwide kufuphi nekliniki").area == "Zwide"
    assert extract_location("ward 99").ward is None          # NMB has 60 wards
    assert extract_location("somewhere unknown").area is None


def test_prompt_files_have_the_same_keys():
    en = json.loads((RESOURCES_DIR / "en.json").read_text(encoding="utf-8"))
    xh = json.loads((RESOURCES_DIR / "xh.json").read_text(encoding="utf-8"))
    en_keys = {k for k in en if not k.startswith("_")}
    xh_keys = {k for k in xh if not k.startswith("_")}
    assert en_keys == xh_keys


def test_placeholders_match_between_languages():
    import re
    en = json.loads((RESOURCES_DIR / "en.json").read_text(encoding="utf-8"))
    xh = json.loads((RESOURCES_DIR / "xh.json").read_text(encoding="utf-8"))
    for key, value in en.items():
        if key.startswith("_"):
            continue
        assert set(re.findall(r"{(\w+)}", value)) == set(re.findall(r"{(\w+)}", xh[key])), key


def test_localised_prompts_and_ussd_variants():
    assert t("xh", "menu.help") == "Uncedo"
    assert t("en", "menu.help") == "Help"
    assert len(t("en", "safety.footer", "ussd")) < len(t("en", "safety.footer"))
    assert t("fr", "menu.help") == "Help"              # unsupported language -> English
    with pytest.raises(MissingPrompt):
        t("en", "no.such.key")
