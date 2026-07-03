"""Module 5 — Featurization: one row per (decision context x candidate place).

This is the pointwise learning-to-rank reduction: each candidate place gets an
independent feature row; the label is 1 for the place actually chosen. Ranking
= sorting candidates by the classifier's P(chosen).

Feature order is a frozen contract (models are persisted against it):

    slot_morning   1 if the decision falls in the morning slot (hour < 12)
    is_weekend     1 for Sat/Sun
    w_sunny/w_rainy/w_hot   one-hot weather bucket (all 0 = unknown weather;
                            the ranker marginalizes over buckets in that case)
    distance_km    haversine from the PRE-MOVE position to the candidate
    frequency      visit_frequency / max over the patient's known places
    familiarity    log1p(avg_stay_time) / log1p(max avg_stay_time)

familiarity is deliberately log-compressed: Module 1 counts an overnight home
dwell as one continuous visit (~757 min vs ~18 min for errand places), so a raw
ratio would make familiarity a home-detector. log1p keeps the ordering but
collapses the 40x gap to ~2x. (Flagged during Phase A review.)

Place stats (frequency / familiarity maxima) must come from TRAIN-WINDOW-ONLY
clusters — see evaluation.freeze_place_stats. Nothing here computes stats.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .weather_provider import BUCKETS
from .recommendation_generation import haversine_km

FEATURE_NAMES: tuple[str, ...] = (
    "slot_morning", "is_weekend", "w_sunny", "w_rainy", "w_hot",
    "distance_km", "frequency", "familiarity",
)
WEATHER_FEATURES: tuple[str, ...] = ("w_sunny", "w_rainy", "w_hot")


@dataclass(frozen=True)
class PlaceStatsNorm:
    """Per-patient normalizers, frozen from the training window's clusters."""
    max_freq: float
    max_log_stay: float

    @classmethod
    def from_places(cls, places: list[dict]) -> "PlaceStatsNorm":
        max_freq = max((p.get("visit_frequency", 0) for p in places), default=0)
        max_log_stay = max(
            (math.log1p(p.get("avg_stay_time", 0.0)) for p in places), default=0.0
        )
        return cls(max_freq=float(max_freq) or 1.0, max_log_stay=max_log_stay or 1.0)


def weather_onehot(bucket: str | None) -> list[float]:
    """One-hot over BUCKETS; all zeros for unknown weather (ranker marginalizes)."""
    return [1.0 if bucket == b else 0.0 for b in BUCKETS] if bucket else [0.0] * len(BUCKETS)


def pair_row(
    *,
    slot_morning: bool,
    is_weekend: bool,
    weather_bucket: str | None,
    current_lat: float,
    current_lng: float,
    place: dict,
    norm: PlaceStatsNorm,
) -> list[float]:
    """Feature row for one (context, candidate place) pair. Order = FEATURE_NAMES."""
    dist = haversine_km(current_lat, current_lng, place["latitude"], place["longitude"])
    freq = place.get("visit_frequency", 0) / norm.max_freq
    familiarity = math.log1p(place.get("avg_stay_time", 0.0)) / norm.max_log_stay
    return [
        1.0 if slot_morning else 0.0,
        1.0 if is_weekend else 0.0,
        *weather_onehot(weather_bucket),
        dist,
        freq,
        familiarity,
    ]


def rows_for_event(event, places: list[dict], norm: PlaceStatsNorm,
                   chosen_cluster_id: int | None = None):
    """All candidate rows for one DecisionEvent (+ labels when training).

    Returns (rows, labels, cluster_ids); labels is None when chosen_cluster_id is.
    """
    rows, labels, cids = [], [], []
    for p in places:
        rows.append(pair_row(
            slot_morning=(event.timestamp.hour < 12),
            is_weekend=event.is_weekend,
            weather_bucket=event.weather_bucket,
            current_lat=event.current_lat,
            current_lng=event.current_lng,
            place=p,
            norm=norm,
        ))
        cids.append(int(p["cluster_id"]))
        if chosen_cluster_id is not None:
            labels.append(1 if int(p["cluster_id"]) == chosen_cluster_id else 0)
    return rows, (labels if chosen_cluster_id is not None else None), cids
