"""Makes a CSV sheet for the fluent isiXhosa reviewer.

Run:  python scripts/language_review_sheet.py
Creates language_review_sheet.csv (open it in Excel or Google Sheets).
Each row: key, English, current isiXhosa, and empty columns for the reviewer.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from report_ec_ai.config import RESOURCES_DIR  # noqa: E402


def main():
    en = json.loads((RESOURCES_DIR / "en.json").read_text(encoding="utf-8"))
    xh = json.loads((RESOURCES_DIR / "xh.json").read_text(encoding="utf-8"))
    lexicon = json.loads((RESOURCES_DIR / "lexicon.json").read_text(encoding="utf-8"))
    out = ROOT / "language_review_sheet.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["type", "key", "English", "isiXhosa (current draft)", "OK? (yes/no)",
                         "Corrected isiXhosa", "Reviewer notes"])
        for key, english in en.items():
            if key.startswith("_") or key in ("welcome", "welcome.ussd"):
                continue
            writer.writerow(["chatbot message", key, english, xh.get(key, "MISSING"), "", "", ""])
        for group in ("complaint_concepts", "verification_cues"):
            for concept in lexicon[group]:
                words = concept.get("xh", []) + [f"{s}- (stem)" for s in concept.get("xh_stems", [])]
                if words:
                    writer.writerow(["vocabulary", concept["id"], ", ".join(concept.get("en", [])[:6]),
                                     ", ".join(words), "", "", ""])
    print(f"Written: {out}")


if __name__ == "__main__":
    main()
