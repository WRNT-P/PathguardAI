# pathguard/backend/app/ai/module5_recommend/recommendation_generation.py
"""Module 5.2 — Recommendation Generation.

Scores each known place for "how likely / relevant is this place right now?"
using a transparent, rule-based blend. All swappable logic lives behind
``score_place`` — replace its body with an ML model later and nothing else in
Module 5 needs to change.

Factors (each normalized to [0, 1]):
  - frequency   : visit_frequency / max(visit_frequency)        — available
  - proximity   : 1 / (1 + distance_km) from current location   — needs location
  - familiarity : avg_stay_time / max(avg_stay_time)            — available
  - time_match  : routine-of-day match                          — STUBBED (no data)

Confidence is the weighted sum of the *active* factors, renormalized by the
sum of active weights, so it stays in [0, 1] no matter which factors are live.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.ai.module1_behavior.routine_patterns import local_hour, probability_at

from .user_context_analysis import UserContext

EARTH_RADIUS_KM = 6371.0

# Tunable blend. Weights are renormalized over whatever is active this request
# (see score_place), so a factor with no data does not quietly drag the
# confidence down — it simply does not vote.
#
# time_match carried weight 0.0 until 2026-08-26 because nothing wrote
# ``routine_patterns``. It now has a writer (module1_behavior/routine_patterns.py)
# and a weight. It sits below frequency deliberately: "she is usually at the
# temple at this hour" is worth less than "she goes to the temple constantly",
# because the routine is inferred from however much history exists while the
# frequency came from the caregiver.
WEIGHTS = {
    "frequency": 0.45,
    "proximity": 0.35,
    "familiarity": 0.20,
    "time_match": 0.25,
}


@dataclass
class ScoredPlace:
    cluster_id: int
    latitude: float
    longitude: float
    visit_frequency: int
    factors: dict          # {frequency, proximity, familiarity, time_match} in [0, 1]
    confidence: float      # [0, 1]
    location_used: bool    # whether proximity contributed
    scorer: str = "rules"  # "rules" | "ml" — never pass rules off as ML
    # What the caregiver called this place. None for anything Module 1 learned:
    # place_clustering.py emits no name and only a human can give one. Module 4
    # has carried it since it was written (TargetLocation.name); Module 5 was
    # dropping it, which left the patient's home screen with coordinates to show
    # a person who cannot read coordinates.
    place_name: str | None = None


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km (same metric Module 1's DBSCAN uses)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def score_place(place: dict, ctx: UserContext, ml_score: float | None = None) -> ScoredPlace:
    """Score one place against the user's current context.

    This is the ML-swappable boundary. When ``ml_score`` is given (the learned
    ranker's P(chosen), computed batch-wise in ``generate_recommendations``),
    it becomes the confidence and the result is flagged ``scorer="ml"``. The
    transparent rule factors are still computed either way — they remain the
    human-readable breakdown; only the confidence source changes.
    """
    # Per-patient maxima for relative normalization (lists are tiny).
    max_freq = max((p.get("visit_frequency", 0) for p in ctx.known_places), default=0)
    max_stay = max((p.get("avg_stay_time", 0.0) for p in ctx.known_places), default=0.0)

    freq = (place.get("visit_frequency", 0) / max_freq) if max_freq else 0.0
    familiarity = (place.get("avg_stay_time", 0.0) / max_stay) if max_stay else 0.0

    if ctx.has_location:
        dist = haversine_km(
            ctx.current_lat, ctx.current_lng,
            place["latitude"], place["longitude"],
        )
        proximity = 1.0 / (1.0 + dist)
    else:
        proximity = 0.0

    # Zero for a patient with no routine on file, and kept out of the blend
    # below in that case rather than counted as "never here at this hour".
    time_match = (
        probability_at(ctx.routine_patterns, local_hour(ctx.now),
                       int(place["cluster_id"]))
        if ctx.has_routine else 0.0
    )

    factors = {
        "frequency": round(freq, 4),
        "proximity": round(proximity, 4),
        "familiarity": round(familiarity, 4),
        "time_match": round(time_match, 4),
    }

    # Only blend factors that are actually active this request.
    active = {"frequency", "familiarity"}
    if ctx.has_location:
        active.add("proximity")
    if ctx.has_routine:
        active.add("time_match")

    weight_sum = sum(WEIGHTS[f] for f in active) or 1.0
    confidence = sum(WEIGHTS[f] * factors[f] for f in active) / weight_sum

    if ml_score is not None:
        confidence, scorer = ml_score, "ml"
    else:
        scorer = "rules"

    return ScoredPlace(
        cluster_id=int(place["cluster_id"]),
        latitude=float(place["latitude"]),
        longitude=float(place["longitude"]),
        visit_frequency=int(place.get("visit_frequency", 0)),
        factors=factors,
        confidence=round(confidence, 4),
        location_used=ctx.has_location,
        scorer=scorer,
        place_name=place.get("place_name") or place.get("name"),
    )


def generate_recommendations(ctx: UserContext, ranker=None) -> list[ScoredPlace]:
    """Score every known place. Empty profile -> empty list.

    With a trained ``ranker`` (see ``ranker.load_ranker``), confidences come
    from the learned model (batch-scored; unknown weather is marginalized).
    Without one, the transparent rule blend applies and results stay flagged
    ``scorer="rules"`` — the fallback is never passed off as ML.
    """
    if ranker is not None and getattr(ranker, "model", None) is not None and ctx.known_places:
        ml_scores = ranker.score_places(
            ctx.known_places, ctx.now, ctx.current_lat, ctx.current_lng
        )
        return [
            score_place(place, ctx, ml_score=round(ml_scores[int(place["cluster_id"])], 4))
            for place in ctx.known_places
        ]
    return [score_place(place, ctx) for place in ctx.known_places]
