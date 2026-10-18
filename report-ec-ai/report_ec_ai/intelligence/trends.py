"""Community trend summaries (Module F).

Input: a list of reports (TrendReport). Output: a JSON-compatible dict.

Privacy rules applied here:
- Only these fields are ever read: reference, category, area, municipality,
  status, created_at, duplicate_of, dispute_count. Anything else (phone,
  name, description, coordinates, photos) is dropped by the TrendReport model.
- The public summary never lists report references inside clusters, and areas
  with fewer than SMALL_COUNT reports are combined into "Other areas".
- Nothing is estimated or invented: every number is a count of the input.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from report_ec_ai.config import MODEL_VERSION, statuses, taxonomy
from report_ec_ai.integration.schemas import TrendReport

SMALL_COUNT = 3          # public areas with fewer reports than this are combined
RECURRING_MIN = 3        # same category in the same area this many times = recurring


def canonical_status(status: str) -> str:
    config = statuses()
    status = (status or "").lower()
    if status in config["lifecycle"]:
        return status
    return config["legacy_aliases"].get(status, "unknown")


def canonical_category(category: str) -> str:
    category = (category or "").lower()
    if category in taxonomy()["categories"]:
        return category
    legacy = taxonomy()["legacy_to_taxonomy"].get(category)
    return legacy["category"] if legacy else "other"


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _week_start(value: datetime) -> str:
    day = _utc(value).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def summarize_trends(reports: list, data_label: str, as_of: Optional[datetime] = None,
                     window_days: int = 30, audience: str = "public",
                     duplicate_clusters: Optional[list] = None) -> dict:
    """`duplicate_clusters` (optional): lists of references grouped by the duplicate
    detector. Links already stored in `duplicate_of` are always included."""
    if audience not in ("public", "municipal"):
        raise ValueError("audience must be 'public' or 'municipal'")
    reports = [r if isinstance(r, TrendReport) else TrendReport.model_validate(r) for r in reports]
    as_of = _utc(as_of or datetime.now(timezone.utc))
    start = as_of - timedelta(days=window_days)
    previous_start = start - timedelta(days=window_days)

    current = [r for r in reports if start < _utc(r.created_at) <= as_of]
    previous = [r for r in reports if previous_start < _utc(r.created_at) <= start]

    closed = set(statuses()["closed_statuses"])
    disputed_statuses = set(statuses()["disputed_statuses"])

    by_category = Counter(canonical_category(r.category) for r in current)
    by_status = Counter(canonical_status(r.status) for r in current)
    by_area_raw = Counter(r.area or "Unknown area" for r in current)
    unresolved = [r for r in current if canonical_status(r.status) not in closed]
    unresolved_by_category = Counter(canonical_category(r.category) for r in unresolved)

    # Areas: exact for municipal staff; small counts combined for the public
    if audience == "public":
        by_area = {}
        other = 0
        for area, count in by_area_raw.items():
            if count >= SMALL_COUNT:
                by_area[area] = count
            else:
                other += count
        if other:
            by_area["Other areas (combined)"] = other
    else:
        by_area = dict(by_area_raw)

    # Recurring: same category in the same area at least RECURRING_MIN times in the window
    pair_counts = Counter((r.area or "Unknown area", canonical_category(r.category)) for r in current)
    recurring = [
        {"area": area, "category": category, "count": count}
        for (area, category), count in pair_counts.most_common() if count >= RECURRING_MIN
    ]

    # Duplicate clusters: stored links + optional AI suggestions (labelled separately)
    by_ref = {r.reference: r for r in current}
    stored = defaultdict(set)
    for r in current:
        if r.duplicate_of and r.duplicate_of in by_ref:
            stored[r.duplicate_of].update({r.duplicate_of, r.reference})

    def describe(members, source):
        first = by_ref[sorted(members)[0]]
        item = {
            "source": source,
            "size": len(members),
            "category": canonical_category(first.category),
            "area": first.area or "Unknown area",
        }
        if audience == "municipal":
            item["references"] = sorted(members)
        return item

    clusters = [describe(m, "linked_by_staff") for m in stored.values()]
    for members in duplicate_clusters or []:
        members = {m for m in members if m in by_ref}
        if len(members) < 2:
            continue
        # Skip AI groups that staff already linked; mark ones that add to a staff link
        if any(members <= linked for linked in stored.values()):
            continue
        item = describe(members, "ai_suggestion_unconfirmed")
        item["overlaps_linked_group"] = any(members & linked for linked in stored.values())
        clusters.append(item)

    disputed = [r for r in current
                if canonical_status(r.status) in disputed_statuses or r.dispute_count > 0]
    awaiting_verification = sum(1 for r in current if canonical_status(r.status) == "pending_verification")

    weeks = defaultdict(Counter)
    for r in current:
        weeks[_week_start(r.created_at)][canonical_category(r.category)] += 1
    over_time = [
        {"week_start": week, "total": sum(counter.values()), "by_category": dict(counter)}
        for week, counter in sorted(weeks.items())
    ]

    change = len(current) - len(previous)
    return {
        "data_label": data_label,
        "audience": audience,
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "total_reports": len(current),
        "by_category": dict(by_category),
        "by_area": by_area,
        "by_status": dict(by_status),
        "unresolved": {"count": len(unresolved), "by_category": dict(unresolved_by_category)},
        "recurring_issues": recurring,
        "potential_duplicate_clusters": clusters,
        "disputed_resolutions": {"count": len(disputed)},
        "resolution_claims_awaiting_verification": awaiting_verification,
        "over_time": over_time,
        "change_vs_previous_window": {
            "current": len(current),
            "previous": len(previous),
            "change": change,
            "percent": round(change / len(previous) * 100, 1) if previous else None,
        },
        "privacy": {
            "fields_used": ["reference", "category", "area", "municipality", "status",
                            "created_at", "duplicate_of", "dispute_count"],
            "small_area_threshold": SMALL_COUNT if audience == "public" else None,
            "references_listed": audience == "municipal",
        },
        "notes": [
            "Counts only; nothing is estimated.",
            "A municipal 'resolved' claim counts as pending verification until the community verification workflow closes it.",
            "Clusters marked ai_suggestion_unconfirmed have not been checked by staff.",
        ],
        "model_version": MODEL_VERSION,
    }
