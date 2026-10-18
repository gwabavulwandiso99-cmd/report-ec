"""Finds existing reports that MIGHT describe the same physical incident.

Score (0-1) = 0.45 x text similarity
            + 0.25 x category match
            + 0.20 x location match
            + 0.10 x time closeness

Text similarity is TF-IDF cosine similarity. Each text is first turned into
"concept words" (e.g. pipe, leak, clinic) using the language layer, so an
English and an isiXhosa report about the same thing can still match.

Hard rules (a candidate is dropped, whatever its text says):
- different parent category
- coordinates more than MAX_DISTANCE_M apart
- different named areas
- more than MAX_DAYS_APART days apart

Two different named landmarks (clinic vs school) do not exclude a match, but
the score is multiplied by DIFFERENT_LANDMARK_PENALTY.

The function only RETURNS suggestions. It never changes, merges or deletes a
report; that decision belongs to municipal staff.
"""
import math
from datetime import datetime, timezone
from typing import Optional

from report_ec_ai.config import places
from report_ec_ai.integration.schemas import DuplicateCandidate, DuplicateResult, ReportForMatching
from report_ec_ai.intelligence.analyzer import ComplaintAnalyzer

WEIGHTS = {"text": 0.45, "category": 0.25, "location": 0.20, "time": 0.10}
STRONG = 0.70
POSSIBLE = 0.50
MAX_DISTANCE_M = 1000
MAX_DAYS_APART = 14
DIFFERENT_LANDMARK_PENALTY = 0.85   # two different named landmarks: probably two places
RESOLVED_STATUSES = {"verified_resolved", "resolved"}


# ---------------------------------------------------------------------------
# Text similarity
# ---------------------------------------------------------------------------
def _cosine_matrix(docs: list):
    """Row 0 compared with every other row. Uses scikit-learn if installed."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        matrix = TfidfVectorizer(token_pattern=r"[^ ]+", sublinear_tf=True).fit_transform(docs)
        return [float(x) for x in cosine_similarity(matrix[0], matrix).ravel()]
    except ImportError:
        return _cosine_plain_python(docs)


def _cosine_plain_python(docs: list):
    """Same idea without scikit-learn (smoothed idf, sublinear tf, l2 norm)."""
    tokenized = [doc.split() for doc in docs]
    n = len(tokenized)
    doc_freq = {}
    for tokens in tokenized:
        for token in set(tokens):
            doc_freq[token] = doc_freq.get(token, 0) + 1
    vectors = []
    for tokens in tokenized:
        counts = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        vec = {}
        for token, count in counts.items():
            idf = math.log((1 + n) / (1 + doc_freq[token])) + 1
            vec[token] = (1 + math.log(count)) * idf
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({k: v / norm for k, v in vec.items()})
    first = vectors[0]
    return [sum(first.get(k, 0.0) * v for k, v in vec.items()) for vec in vectors]


# ---------------------------------------------------------------------------
# Location and time
# ---------------------------------------------------------------------------
def valid_coordinates(lat: Optional[float], lng: Optional[float]) -> bool:
    if lat is None or lng is None:
        return False
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return False
    if lat == 0 and lng == 0:
        return False
    box = places()["eastern_cape_bounds"]
    return box["min_lat"] <= lat <= box["max_lat"] and box["min_lng"] <= lng <= box["max_lng"]


def distance_m(lat1, lng1, lat2, lng2) -> float:
    """Haversine distance in metres."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class DuplicateDetector:
    def __init__(self, analyzer: Optional[ComplaintAnalyzer] = None):
        self.analyzer = analyzer or ComplaintAnalyzer()
        self._analyses = {}   # description -> analysis, so a text is only analysed once

    def _profile(self, report: ReportForMatching) -> dict:
        """What we compare: concept words, category and landmark (filled from the text if missing)."""
        if report.description not in self._analyses:
            self._analyses[report.description] = self.analyzer.analyze(report.description)
        analysis = self._analyses[report.description]
        concepts = [m.concept for m in analysis.matched_terms]
        category = report.category or analysis.category.category
        subcategory = report.subcategory or analysis.category.subcategory
        landmark = report.landmark or analysis.location_hints.landmark
        area = report.area or analysis.location_hints.area
        words = analysis.normalized_text.split()
        # Concept words count double, so meaning matters more than exact wording
        doc = " ".join(concepts * 2 + [f"sub_{subcategory}"] * 2 + words)
        return {
            "doc": doc or "empty",
            "category": category,
            "subcategory": subcategory,
            "landmark": landmark,
            "area": area.lower() if area else None,
        }

    def find(self, new_report: ReportForMatching, existing: list) -> DuplicateResult:
        new = self._profile(new_report)
        new_time = _as_utc(new_report.created_at)
        excluded = {"same_reference": 0, "different_category": 0, "too_far_apart": 0,
                    "different_area": 0, "too_long_ago": 0, "low_score": 0}

        # First pass: hard rules
        kept = []
        for report in existing:
            if report.reference == new_report.reference:
                excluded["same_reference"] += 1
                continue
            other = self._profile(report)
            hours = abs((new_time - _as_utc(report.created_at)).total_seconds()) / 3600
            if hours > MAX_DAYS_APART * 24:
                excluded["too_long_ago"] += 1
                continue
            if new["category"] != other["category"]:
                excluded["different_category"] += 1
                continue
            dist = None
            if valid_coordinates(new_report.latitude, new_report.longitude) and \
                    valid_coordinates(report.latitude, report.longitude):
                dist = distance_m(new_report.latitude, new_report.longitude, report.latitude, report.longitude)
                if dist > MAX_DISTANCE_M:
                    excluded["too_far_apart"] += 1
                    continue
            elif new["area"] and other["area"] and new["area"] != other["area"]:
                excluded["different_area"] += 1
                continue
            kept.append((report, other, hours, dist))

        if not kept:
            return DuplicateResult(new_reference=new_report.reference, candidates=[],
                                   checked=len(existing), excluded=excluded)

        similarities = _cosine_matrix([new["doc"]] + [o["doc"] for _, o, _, _ in kept])[1:]

        candidates = []
        for (report, other, hours, dist), text_sim in zip(kept, similarities):
            reasons = [f"Text meaning similarity {text_sim:.2f}."]
            if new["subcategory"] == other["subcategory"]:
                category_score = 1.0
                reasons.append(f"Same problem type ({other['category']}/{other['subcategory']}).")
            else:
                category_score = 0.6
                reasons.append(f"Same category ({other['category']}), different subtype.")

            if dist is not None:
                location_score = 1.0 if dist <= 150 else 0.7 if dist <= 500 else 0.3
                reasons.append(f"About {dist:.0f} m apart.")
            else:
                same_landmark = new["landmark"] and new["landmark"] == other["landmark"]
                different_landmark = new["landmark"] and other["landmark"] and not same_landmark
                same_area = new["area"] and new["area"] == other["area"]
                if different_landmark:
                    location_score = 0.0
                    reasons.append(f"Different landmarks ({new['landmark']} / {other['landmark']}).")
                elif same_landmark and same_area:
                    location_score = 1.0
                    reasons.append(f"Same area and landmark ({other['landmark']}).")
                elif same_landmark:
                    location_score = 0.6
                    reasons.append(f"Same landmark ({other['landmark']}); area not confirmed for both.")
                elif same_area:
                    location_score = 0.4
                    reasons.append("Same area, no shared landmark.")
                else:
                    location_score = 0.0
                    reasons.append("Location could not be compared.")

            time_score = 1.0 if hours <= 48 else 0.5 if hours <= 7 * 24 else 0.2
            reasons.append(f"Submitted {hours:.0f} hours apart.")

            score = (WEIGHTS["text"] * text_sim + WEIGHTS["category"] * category_score
                     + WEIGHTS["location"] * location_score + WEIGHTS["time"] * time_score)
            if dist is None and new["landmark"] and other["landmark"] and new["landmark"] != other["landmark"]:
                score *= DIFFERENT_LANDMARK_PENALTY
            score = round(score, 3)
            if score < POSSIBLE:
                excluded["low_score"] += 1
                continue

            relation = "possible_same_incident"
            if (report.status or "").lower() in RESOLVED_STATUSES:
                relation = "possible_recurrence"
                reasons.append("The earlier report was marked resolved; this may be the problem coming back.")

            candidates.append(DuplicateCandidate(
                reference=report.reference,
                score=score,
                label="strong_candidate" if score >= STRONG else "possible_candidate",
                relation=relation,
                components={"text": round(text_sim, 3), "category": category_score,
                            "location": location_score, "time": time_score},
                reasons=reasons,
                distance_m=round(dist, 1) if dist is not None else None,
                hours_apart=round(hours, 1),
            ))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return DuplicateResult(new_reference=new_report.reference, candidates=candidates,
                               checked=len(existing), excluded=excluded)

    def clusters(self, reports: list) -> list:
        """Groups of reports that are linked by strong or possible matches (for trend summaries)."""
        parent = {r.reference: r.reference for r in reports}

        def root(ref):
            while parent[ref] != ref:
                parent[ref] = parent[parent[ref]]
                ref = parent[ref]
            return ref

        for i, report in enumerate(reports):
            others = reports[i + 1:]
            if not others:
                continue
            for candidate in self.find(report, others).candidates:
                parent[root(candidate.reference)] = root(report.reference)

        groups = {}
        for report in reports:
            groups.setdefault(root(report.reference), []).append(report.reference)
        return [sorted(members) for members in groups.values() if len(members) > 1]


def find_potential_duplicates(new_report: ReportForMatching, existing: list) -> DuplicateResult:
    return DuplicateDetector().find(new_report, existing)
