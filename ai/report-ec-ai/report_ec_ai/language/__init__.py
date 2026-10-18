"""English, isiXhosa and code-switched language processing for Report EC."""
from report_ec_ai.language.detection import LanguageGuess, detect_language
from report_ec_ai.language.locations import LocationHints, extract_location
from report_ec_ai.language.normalization import NormalizedText, clean_input, normalize
from report_ec_ai.language.vocabulary import (
    ConceptMatch,
    complaint_vocabulary,
    verification_vocabulary,
)

__all__ = [
    "ConceptMatch",
    "LanguageGuess",
    "LocationHints",
    "NormalizedText",
    "clean_input",
    "complaint_vocabulary",
    "detect_language",
    "extract_location",
    "normalize",
    "verification_vocabulary",
]
