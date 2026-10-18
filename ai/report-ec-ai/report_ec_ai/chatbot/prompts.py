"""Localised chatbot messages.

`t(language, key, channel)` looks the key up in en.json / xh.json. For USSD it
first tries "key.ussd" (a shorter version). If a key is missing in isiXhosa it
falls back to English, and a test makes sure that never happens unnoticed.
"""
from report_ec_ai.config import SETTINGS, messages


class MissingPrompt(KeyError):
    pass


def t(language: str, key: str, channel: str = "whatsapp", **values) -> str:
    language = language if language in SETTINGS.supported_languages else SETTINGS.default_language
    table = messages(language)
    english = messages("en")
    candidates = [f"{key}.ussd", key] if channel == "ussd" else [key]
    for candidate in candidates:
        if candidate in table:
            return table[candidate].format(**values)
        if candidate in english:
            return english[candidate].format(**values)
    raise MissingPrompt(key)


def menu(language: str, header_key: str, option_keys: list, channel: str = "whatsapp", **values) -> str:
    """Header plus numbered options, e.g. '1. Submit\\n2. Edit'."""
    lines = [t(language, header_key, channel, **values)] if header_key else []
    separator = " " if channel == "ussd" else ". "
    for number, key in enumerate(option_keys, start=1):
        lines.append(f"{number}{separator}{t(language, key, channel)}")
    return "\n".join(lines)


def options_only(language: str, option_keys: list, channel: str = "whatsapp") -> str:
    return menu(language, None, option_keys, channel)
