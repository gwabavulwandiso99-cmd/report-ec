"""Loads the JSON configuration files and holds the settings used across the package.

All the word lists, categories and statuses live in JSON so that teammates can
change them without editing Python code.
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PACKAGE_DIR / "config"
RESOURCES_DIR = PACKAGE_DIR / "language" / "resources"
DATA_DIR = PACKAGE_DIR / "data"

# Shown on every AI output so nobody mistakes a suggestion for a decision
ADVISORY_NOTICE = (
    "AI suggestion only. Not a municipal decision. "
    "Staff must review it before acting on it."
)

# Rule-based models have a version too, so a report can show which rules judged it
MODEL_VERSION = "rules-2026.10-v1"


@lru_cache(maxsize=None)
def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def taxonomy() -> dict:
    return load_json(str(CONFIG_DIR / "taxonomy.json"))


def statuses() -> dict:
    return load_json(str(CONFIG_DIR / "statuses.json"))


def places() -> dict:
    return load_json(str(CONFIG_DIR / "places.json"))


def lexicon() -> dict:
    return load_json(str(RESOURCES_DIR / "lexicon.json"))


def messages(language: str) -> dict:
    return load_json(str(RESOURCES_DIR / f"{language}.json"))


@dataclass(frozen=True)
class Settings:
    """Numbers that control the chatbot. Change them here, not inside the code."""

    supported_languages: tuple = ("en", "xh")
    default_language: str = "en"
    max_message_chars: int = 1000          # longer messages are refused, not cut
    max_description_chars: int = 1000
    session_idle_minutes: int = 30         # after this the bot offers to resume
    draft_ttl_hours: int = 24              # unfinished reports are dropped after this
    max_invalid_attempts: int = 3          # after this the bot suggests "help"
    max_location_attempts: int = 2
    remembered_message_ids: int = 50       # used to ignore repeated webhook deliveries
    ussd_max_chars: int = 182              # one USSD screen
    allowed_photo_types: tuple = ("image/jpeg", "image/png", "image/webp")
    max_evidence_items: int = 3


SETTINGS = Settings()
