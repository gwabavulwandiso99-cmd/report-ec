"""Recognises commands, menu choices and reference numbers in resident messages.

Commands only count when they are the WHOLE message ("cancel", "rhoxisa"), so a
complaint such as "the water stopped" is never mistaken for "stop".
"""
import re
from enum import Enum
from typing import Optional

from report_ec_ai.language.normalization import normalize


class Command(str, Enum):
    HELP = "help"
    CANCEL = "cancel"
    BACK = "back"
    RESTART = "restart"
    MENU = "menu"
    LANGUAGE = "language"
    ENGLISH = "english"
    XHOSA = "isixhosa"
    GREETING = "greeting"


COMMAND_WORDS = {
    Command.HELP: {"help", "help me", "ncedo", "uncedo", "nceda"},
    Command.CANCEL: {"cancel", "stop", "quit", "exit", "rhoxisa", "yeka", "misa"},
    Command.BACK: {"back", "go back", "previous", "emva", "buyela", "buyela emva", "buyela umva"},
    Command.RESTART: {"restart", "start again", "start over", "qala", "qala kwakhona", "qalisa kwakhona"},
    Command.MENU: {"menu", "main menu", "home", "imenyu", "menyu"},
    Command.LANGUAGE: {"language", "change language", "ulwimi", "tshintsha ulwimi"},
    Command.ENGLISH: {"english", "eng", "isingesi"},
    Command.XHOSA: {"isixhosa", "xhosa", "isixhosa please", "ixhosa"},
    Command.GREETING: {"hi", "hello", "hey", "hallo", "good morning", "good day", "molo", "molweni",
                       "molo sisi", "molo bhuti", "sawubona", "start", "report ec"},
}

YES_WORDS = {"yes", "y", "yebo", "ewe", "ok", "okay", "submit", "send", "confirm", "thumela"}
SKIP_WORDS = {"0", "skip", "no", "none", "nope", "n", "hayi", "tsiba", "akukho", "no photo",
              "don't know", "dont know", "i don't know", "i dont know", "not sure", "andazi", "andiyazi"}

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9}
_EMOJI_DIGITS = {"1️⃣": 1, "2️⃣": 2, "3️⃣": 3, "4️⃣": 4, "5️⃣": 5, "6️⃣": 6, "7️⃣": 7, "8️⃣": 8, "9️⃣": 9}
_CHOICE = re.compile(r"^(?:option|number|no\.?|#)?\s*(\d{1,2})\s*[.)]?$")
_REFERENCE = re.compile(r"\b((?:mock[\s-]?)?ec)[\s-]?(\d{3,8})\b", re.IGNORECASE)


def _simple(text: Optional[str]) -> str:
    return normalize(text or "", fuzzy=False).normalized


def detect_command(text: Optional[str]) -> Optional[Command]:
    simple = _simple(text)
    if not simple:
        return None
    for command, words in COMMAND_WORDS.items():
        if simple in words:
            return command
    return None


def parse_choice(text: Optional[str], options: int) -> Optional[int]:
    """'2', '2.', 'option 2', 'two', '2️⃣' -> 2. Returns None if not a valid option."""
    if text is None:
        return None
    raw = text.strip()
    if raw in _EMOJI_DIGITS:
        number = _EMOJI_DIGITS[raw]
    else:
        simple = raw.lower().strip()
        if simple in _NUMBER_WORDS:
            number = _NUMBER_WORDS[simple]
        else:
            found = _CHOICE.match(simple)
            if not found:
                return None
            number = int(found.group(1))
    return number if 1 <= number <= options else None


def is_yes(text: Optional[str]) -> bool:
    return _simple(text) in YES_WORDS


def is_skip(text: Optional[str]) -> bool:
    raw = (text or "").strip().lower()
    return raw in SKIP_WORDS or _simple(text) in SKIP_WORDS


def parse_reference(text: Optional[str]) -> Optional[str]:
    """'ec 1042', 'EC-1042', 'mock-ec-1001' -> 'EC-1042' / 'MOCK-EC-1001'."""
    found = _REFERENCE.search(text or "")
    if not found:
        return None
    prefix = "MOCK-EC" if found.group(1).lower().startswith("mock") else "EC"
    return f"{prefix}-{found.group(2)}"


def parse_ward(text: Optional[str], max_ward: int) -> Optional[str]:
    simple = _simple(text)
    found = re.search(r"\d{1,3}", simple)
    if not found:
        return None
    number = int(found.group(0))
    return str(number) if 1 <= number <= max_ward else None
