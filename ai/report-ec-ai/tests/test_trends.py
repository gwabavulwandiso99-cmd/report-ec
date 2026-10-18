"""Trend summaries (Module F; required tests 22 and 23)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from report_ec_ai.config import DATA_DIR
from report_ec_ai.intelligence.trends import canonical_status, summarize_trends

AS_OF = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def r(ref, category, area, status, days_ago, **extra):
    return {"reference": ref, "category": category, "area": area, "status": status,
            "created_at": (AS_OF - timedelta(days=days_ago)).isoformat(), **extra}


REPORTS = [
    r("A1", "water", "Zwide", "submitted", 1),
    r("A2", "water", "Zwide", "in_progress", 2, duplicate_of="A1"),
    r("A3", "water", "Zwide", "verified_resolved", 3),
    r("A4", "electricity", "KwaZakhele", "disputed", 4, dispute_count=2),
    r("A5", "power_outage", "KwaZakhele", "resolved", 5),          # legacy names
    r("A6", "roads", "Motherwell", "pending_verification", 6),
    r("A7", "waste", "Korsten", "acknowledged", 40),               # previous window
]


# 22. Trend summary correctness
def test_counts_are_correct():
    s = summarize_trends(REPORTS, "test data", as_of=AS_OF, window_days=30, audience="municipal")
    assert s["total_reports"] == 6
    assert s["by_category"] == {"water": 3, "electricity": 2, "roads": 1}
    assert s["by_area"] == {"Zwide": 3, "KwaZakhele": 2, "Motherwell": 1}
    assert s["by_status"] == {"submitted": 1, "in_progress": 1, "verified_resolved": 1,
                              "disputed": 1, "pending_verification": 2}
    assert s["unresolved"]["count"] == 5
    assert s["disputed_resolutions"]["count"] == 1
    assert s["resolution_claims_awaiting_verification"] == 2
    assert s["recurring_issues"] == [{"area": "Zwide", "category": "water", "count": 3}]
    assert s["potential_duplicate_clusters"] == [
        {"source": "linked_by_staff", "size": 2, "category": "water", "area": "Zwide", "references": ["A1", "A2"]}]
    assert s["change_vs_previous_window"] == {"current": 6, "previous": 1, "change": 5, "percent": 500.0}
    assert sum(week["total"] for week in s["over_time"]) == 6
    assert s["data_label"] == "test data"


def test_legacy_resolved_counts_as_a_claim_not_a_verified_fix():
    assert canonical_status("resolved") == "pending_verification"
    assert canonical_status("received") == "submitted"
    assert canonical_status("nonsense") == "unknown"


# 23. Public data privacy
def test_public_summary_contains_no_personal_data():
    private = [dict(x, phone="27820000001", reporter_name="Test Person",
                    description="Outside my house at 12 Example Street",
                    latitude=-33.87, longitude=25.56, photo_url="https://example.org/p.jpg")
               for x in REPORTS]
    s = summarize_trends(private, "test data", as_of=AS_OF, audience="public")
    text = json.dumps(s)
    for secret in ("27820000001", "Test Person", "Example Street", "-33.87", "example.org", "A1", "A2"):
        assert secret not in text
    # Areas with fewer than 3 reports are combined in public output
    assert s["by_area"] == {"Zwide": 3, "Other areas (combined)": 3}
    assert "references" not in s["potential_duplicate_clusters"][0]


def test_empty_input_and_bad_audience():
    s = summarize_trends([], "test data", as_of=AS_OF)
    assert s["total_reports"] == 0
    assert s["change_vs_previous_window"]["percent"] is None
    with pytest.raises(ValueError):
        summarize_trends([], "x", audience="everyone")


def test_ai_clusters_are_labelled_unconfirmed():
    s = summarize_trends(REPORTS, "t", as_of=AS_OF, audience="municipal",
                         duplicate_clusters=[["A4", "A5"]])
    sources = {c["source"] for c in s["potential_duplicate_clusters"]}
    assert sources == {"linked_by_staff", "ai_suggestion_unconfirmed"}


def test_sample_data_is_labelled_and_summarises():
    data = json.loads((DATA_DIR / "sample_complaints.json").read_text(encoding="utf-8"))
    assert "SYNTHETIC" in data["_notice"]
    s = summarize_trends(data["reports"], data["data_label"], as_of=datetime.fromisoformat(data["as_of"]))
    assert s["data_label"].startswith("SAMPLE DATA")
    assert s["total_reports"] > 0
