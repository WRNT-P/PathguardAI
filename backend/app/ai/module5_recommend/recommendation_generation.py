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

from .user_context_analysis import UserContext

EARTH_RADIUS_KM = 6371.0

# Tunable blend. time_match is wired but disabled (weight 0) because Module 1
# does not yet populate ``routine_patterns``.
# TODO: raise time_match weight once Module 1 writes routine_patterns.
WEIGHTS = {
    "frequency": 0.45,
    "proximity": 0.35,
    "familiarity": 0.20,
    "time_match": 0.0,
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

    time_match = 0.0  # no routine_patterns data yet

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
    # time_match stays out while its weight is 0.

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
