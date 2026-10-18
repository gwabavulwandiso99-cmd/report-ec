"""Measures the rule-based categoriser and priority engine on the SYNTHETIC examples.

Run:  python evaluate.py              (development set)
      python evaluate.py --holdout    (hold-out set, see its _notice)

Read the results carefully: the examples were written by the same team that
wrote the word lists, so these numbers are optimistic. They show whether the
rules behave as intended, not how well they work on real reports.
"""
import json
import sys
from collections import Counter, defaultdict

from report_ec_ai.config import DATA_DIR
from report_ec_ai.intelligence.analyzer import analyze_complaint


def main():
    name = "synthetic_holdout.json" if "--holdout" in sys.argv else "synthetic_eval.json"
    data = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    print(f"File: {name}")
    print(data["_notice"])
    print()

    per_language = defaultdict(lambda: Counter())
    confusion = Counter()
    misses = []
    for example in data["examples"]:
        result = analyze_complaint(example["text"])
        category = result.category.category
        priority = result.priority.priority
        lang = example["language"]
        cat_ok = category == example["expected_category"]
        pri_ok = priority in example["expected_priority"]
        per_language[lang]["total"] += 1
        per_language[lang]["category_ok"] += cat_ok
        per_language[lang]["priority_ok"] += pri_ok
        per_language[lang]["clarify"] += result.needs_clarification
        confusion[(example["expected_category"], category)] += 1
        if not (cat_ok and pri_ok):
            misses.append((example["id"], example["text"], example["expected_category"], category,
                           example["expected_priority"], priority))

    print(f"{'set':8} {'n':>3} {'category':>10} {'priority':>10} {'asks to clarify':>16}")
    totals = Counter()
    for lang in ("en", "xh", "mixed"):
        c = per_language[lang]
        totals.update(c)
        print(f"{lang:8} {c['total']:>3} {c['category_ok'] / c['total']:>10.0%} "
              f"{c['priority_ok'] / c['total']:>10.0%} {c['clarify']:>16}")
    print(f"{'all':8} {totals['total']:>3} {totals['category_ok'] / totals['total']:>10.0%} "
          f"{totals['priority_ok'] / totals['total']:>10.0%} {totals['clarify']:>16}")

    print("\nCategory confusion (expected -> predicted), mistakes only:")
    for (expected, predicted), count in sorted(confusion.items()):
        if expected != predicted:
            print(f"  {expected:22} -> {predicted:22} x{count}")

    print("\nExamples that missed category or priority:")
    for row in misses:
        print(f"  [{row[0]}] {row[1]}\n      category {row[2]} -> {row[3]}; priority {row[4]} -> {row[5]}")


if __name__ == "__main__":
    main()
