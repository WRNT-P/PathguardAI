"""Module 5 — recommendation context, scoring, and prioritization (pure)."""
from datetime import datetime

import pytest

from app.ai.module5_recommend.recommendation_generation import (
    WEIGHTS,
    generate_recommendations,
    score_place,
)
from app.ai.module5_recommend.recommendation_prioritization import prioritize
from app.ai.module5_recommend.user_context_analysis import (
    UserContext,
    build_user_context,
)

NOW = datetime(2026, 6, 29, 10, 0, 0)

PLACES = [
    {"cluster_id": 0, "latitude": 13.7500, "longitude": 100.5000,
     "visit_frequency": 40, "avg_stay_time": 120.0},
    {"cluster_id": 1, "latitude": 13.7600, "longitude": 100.5100,
     "visit_frequency": 10, "avg_stay_time": 30.0},
]


class _ProfileRow:
    def __init__(self, patient_id, known_places):
        self.patient_id = patient_id
        self.known_places = known_places


# ── build_user_context ────────────────────────────────────────────────────────

def test_build_context_parses_json_places():
    import json

    profile = _ProfileRow(7, json.dumps(PLACES))
    ctx = build_user_context(profile, 13.75, 100.5, NOW)
    assert ctx.patient_id == 7
    assert ctx.has_profile is True
    assert ctx.has_location is True
    assert len(ctx.known_places) == 2


def test_build_context_none_profile_is_empty():
    ctx = build_user_context(None, None, None, NOW)
    assert ctx.patient_id == 0
    assert ctx.has_profile is False
    assert ctx.has_location is False
    assert ctx.known_places == []


def test_build_context_tolerates_malformed_json():
    profile = _ProfileRow(1, "{not valid json")
    ctx = build_user_context(profile, 13.75, 100.5, NOW)
    assert ctx.known_places == []


# ── score_place ───────────────────────────────────────────────────────────────

def test_score_place_with_location_blends_active_factors():
    ctx = UserContext(1, 13.7500, 100.5000, NOW, known_places=PLACES)
    scored = score_place(PLACES[0], ctx)
    assert scored.location_used is True
    # On top of the place -> proximity ~1, top frequency & stay -> 1.0
    assert scored.factors["frequency"] == pytest.approx(1.0)
    assert scored.factors["familiarity"] == pytest.approx(1.0)
    assert scored.factors["proximity"] == pytest.approx(1.0, abs=1e-3)
    assert scored.confidence == pytest.approx(1.0, abs=1e-3)


def test_score_place_confidence_in_unit_interval():
    ctx = UserContext(1, 13.7500, 100.5000, NOW, known_places=PLACES)
    for place in PLACES:
        s = score_place(place, ctx)
        assert 0.0 <= s.confidence <= 1.0


def test_score_place_without_location_excludes_proximity():
    ctx = UserContext(1, None, None, NOW, known_places=PLACES)
    scored = score_place(PLACES[0], ctx)
    assert scored.location_used is False
    assert scored.factors["proximity"] == 0.0
    # Confidence renormalizes over {frequency, familiarity} only.
    expected = (
        WEIGHTS["frequency"] * scored.factors["frequency"]
        + WEIGHTS["familiarity"] * scored.factors["familiarity"]
    ) / (WEIGHTS["frequency"] + WEIGHTS["familiarity"])
    assert scored.confidence == pytest.approx(round(expected, 4))


def test_generate_recommendations_empty_profile():
    ctx = UserContext(1, 13.75, 100.5, NOW, known_places=[])
    assert generate_recommendations(ctx) == []


# ── prioritize ────────────────────────────────────────────────────────────────

def test_prioritize_sorts_by_confidence_desc_and_truncates():
    ctx = UserContext(1, 13.7500, 100.5000, NOW, known_places=PLACES)
    scored = generate_recommendations(ctx)
    ranked = prioritize(scored, top_n=1)
    assert len(ranked) == 1
    assert ranked[0].cluster_id == 0  # nearest + most frequent


def test_prioritize_tie_breaks_on_visit_frequency():
    ctx = UserContext(1, None, None, NOW, known_places=PLACES)
    a = score_place(PLACES[0], ctx)
    b = score_place(PLACES[1], ctx)
    # Force a confidence tie so the visit_frequency tie-break decides.
    object.__setattr__(b, "confidence", a.confidence)
    ranked = prioritize([b, a], top_n=2)
    assert ranked[0].visit_frequency >= ranked[1].visit_frequency
    assert ranked[0].cluster_id == 0
