"""Potential duplicate detection (Module D; required tests 18 and 19)."""
import copy
from datetime import datetime, timedelta, timezone

from report_ec_ai.integration.schemas import ReportForMatching as Report
from report_ec_ai.intelligence import duplicates
from report_ec_ai.intelligence.duplicates import DuplicateDetector, find_potential_duplicates

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


def report(ref, text, hours_ago=1, **extra):
    return Report(reference=ref, description=text, created_at=NOW - timedelta(hours=hours_ago), **extra)


CLINIC_REPORTS = [
    report("EC-1", "Burst pipe near the clinic", 6, area="Zwide"),
    report("EC-2", "Water leaking outside the clinic", 4, area="Zwide"),
    report("EC-3", "Kukho umbhobho wamanzi ogqabhukileyo kufuphi nekliniki", 2, area="Zwide"),
]


# 18. Duplicate matching
def test_related_reports_are_suggested():
    new = report("EC-NEW", "Broken water pipe at the clinic entrance", 0, area="Zwide")
    result = find_potential_duplicates(new, CLINIC_REPORTS)
    refs = [c.reference for c in result.candidates]
    assert set(refs) == {"EC-1", "EC-2", "EC-3"}           # incl. the isiXhosa one
    for candidate in result.candidates:
        assert candidate.requires_human_confirmation is True
        assert candidate.relation == "possible_same_incident"
        assert candidate.reasons
        assert 0 <= candidate.score <= 1
    assert "never merged" in result.advisory_notice


# 19. Different-location false-positive avoidance
def test_same_text_in_a_different_area_is_not_a_duplicate():
    new = report("EC-NEW", "Burst pipe near the clinic", 0, area="Motherwell")
    result = find_potential_duplicates(new, [report("EC-1", "Burst pipe near the clinic", 1, area="Zwide")])
    assert result.candidates == []
    assert result.excluded["different_area"] == 1


def test_same_text_far_apart_by_coordinates_is_not_a_duplicate():
    new = report("EC-NEW", "Burst pipe near the clinic", 0, latitude=-33.8705, longitude=25.5602)
    far = report("EC-1", "Burst pipe near the clinic", 1, latitude=-33.8030, longitude=25.5950)  # ~8 km
    near = report("EC-2", "Burst pipe near the clinic", 1, latitude=-33.8707, longitude=25.5605)  # ~40 m
    result = find_potential_duplicates(new, [far, near])
    assert [c.reference for c in result.candidates] == ["EC-2"]
    assert result.candidates[0].distance_m < 100
    assert result.excluded["too_far_apart"] == 1


def test_different_landmark_or_category_or_time_is_excluded():
    new = report("EC-NEW", "Water leak near the clinic", 0, area="Zwide")
    others = [
        report("EC-1", "Water leak at the school", 1, area="Zwide"),          # other landmark
        report("EC-2", "Pothole near the clinic", 1, area="Zwide"),            # other category
        report("EC-3", "Water leak near the clinic", 24 * 30, area="Zwide"),   # a month ago
    ]
    result = find_potential_duplicates(new, others)
    assert result.candidates == []
    assert result.excluded["different_category"] == 1
    assert result.excluded["too_long_ago"] == 1


def test_resolved_report_is_marked_as_possible_recurrence():
    new = report("EC-NEW", "Burst pipe near the clinic", 0, area="Zwide")
    old = report("EC-1", "Burst pipe near the clinic", 100, area="Zwide", status="verified_resolved")
    result = find_potential_duplicates(new, [old])
    assert result.candidates[0].relation == "possible_recurrence"


def test_original_reports_are_never_changed():
    before = copy.deepcopy([r.model_dump() for r in CLINIC_REPORTS])
    find_potential_duplicates(report("EC-NEW", "Broken water pipe at the clinic", 0, area="Zwide"), CLINIC_REPORTS)
    assert [r.model_dump() for r in CLINIC_REPORTS] == before


def test_invalid_coordinates_are_ignored():
    assert not duplicates.valid_coordinates(0, 0)
    assert not duplicates.valid_coordinates(51.5, -0.12)        # London, outside the Eastern Cape
    assert not duplicates.valid_coordinates(None, 25.5)
    assert duplicates.valid_coordinates(-33.87, 25.56)


def test_clusters_group_related_reports():
    groups = DuplicateDetector().clusters(CLINIC_REPORTS + [report("EC-9", "Pothole on Mendi road", 1, area="Zwide")])
    assert groups == [["EC-1", "EC-2", "EC-3"]]


def test_works_without_scikit_learn(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("sklearn"):
            raise ImportError("pretend scikit-learn is missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    new = report("EC-NEW", "Broken water pipe at the clinic entrance", 0, area="Zwide")
    result = find_potential_duplicates(new, CLINIC_REPORTS)
    assert {c.reference for c in result.candidates} == {"EC-1", "EC-2", "EC-3"}


def test_plain_python_tfidf_matches_scikit_learn():
    docs = ["pipe leak clinic", "pipe burst clinic", "pothole road", "pipe leak clinic water"]
    plain = duplicates._cosine_plain_python(docs)
    learned = duplicates._cosine_matrix(docs)
    for a, b in zip(plain, learned):
        assert abs(a - b) < 1e-6


def test_empty_existing_list():
    result = find_potential_duplicates(report("EC-NEW", "Burst pipe", 0), [])
    assert result.candidates == [] and result.checked == 0
