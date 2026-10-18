"""Checks on untrusted resident text.

Resident messages are DATA. Nothing a resident writes can change a status,
priority or decision in this package, because no part of the code reads the
text as a command. This module only flags messages that look like attempts to
give the system instructions, so staff can see them.
"""
import re

_INSTRUCTION_PATTERNS = [
    r"\bignore (?:all |the )?(?:previous|above|earlier|prior) (?:instructions|rules|messages)\b",
    r"\b(?:system|developer) prompt\b",
    r"\byou are now\b",
    r"\bact as (?:an? )?(?:admin|administrator|system)\b",
    r"\b(?:mark|set|change|update) (?:this|the|my|it|report)?\s*(?:report|ticket|complaint|issue|status|priority)?\s*(?:as|to) (?:resolved|fixed|closed|critical|urgent|verified)\b",
    r"\bset (?:the )?(?:status|priority)\b",
    r"\b(?:delete|drop|remove|erase) (?:all|the|this|every|other)\b.*\b(?:reports?|complaints?|table|database|records?)\b",
    r"\bdrop table\b",
    r";\s*--",
    r"<\s*script",
    r"\bsudo\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INSTRUCTION_PATTERNS]


def looks_like_instruction(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _COMPILED)
