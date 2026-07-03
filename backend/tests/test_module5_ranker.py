"""Module 5 learned ranker — data source, harness guards, model, integration.

All on SIMULATED data (MockDataSource). These tests pin the honesty-critical
mechanics: generator determinism and caps, the temporal split, the
train-window-frozen place stats (leakage guard), the ranker round-trip, and the
flagged rule-based fallback in score_place / generate_recommendations.
"""
from datetime import datetime, timedelta

import numpy as np
import pytest

from app.ai.module5_recommend.data_source import (
    BASE_DATE, OFF_PATTERN_RATE, PLACE_KEYS, MockDataSource,
)
from app.ai.module5_recommend.evaluation import (
    freeze_place_stats, labels_for, majority_cluster, temporal_split,
)
from app.ai.module5_recommend.featurize import FEATURE_NAMES, PlaceStatsNorm, pair_row
from app.ai.module5_recommend.ranker import Module5Ranker, load_ranker
from app.ai.module5_recommend.recommendation_generation import (
    generate_recommendations, score_place,
)
from app.ai.module5_recommend.user_context_analysis import UserContext


@pytest.fixture(scope="module")
def source():
    return MockDataSource(n_days=90, seed=42)


@pytest.fixture(scope="module")
def split(source):
    return temporal_split(source, cut_day=63)


@pytest.fixture(scope="module")
def frozen(split):
    return freeze_place_stats(split)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def test_generator_deterministic(source):
    again = MockDataSource(n_days=90, seed=42)
    a, b = source.decision_events(), again.decision_events()
    assert len(a) == len(b) == 180
    assert all(
        (x.chosen_place_key, x.weather_bucket, x.timestamp) ==
        (y.chosen_place_key, y.weather_bucket, y.timestamp)
        for x, y in zip(a, b)
    )


def test_generator_probabilistic_caps(source):
    """No context distribution may be deterministic; winner capped at ~0.65
    before off-pattern mixing (the anti-circularity knob)."""
    for slot in ("morning", "evening"):
        for wknd in (False, True):
            for w in ("sunny", "rainy", "hot"):
                dist = source.effective_distribution(slot, wknd, w)
                assert abs(sum(dist.values()) - 1.0) < 1e-9
                cap = 0.65 * (1 - OFF_PATTERN_RATE) + OFF_PATTERN_RATE / len(PLACE_KEYS)
                assert max(dist.values()) <= cap + 1e-9


def test_pre_move_position_never_chosen_place(source):
    """Proximity-leakage guard: context position is home, not the destination."""
    for e in source.decision_events():
        assert (e.current_lat, e.current_lng) == (13.75, 100.50)


# ---------------------------------------------------------------------------
# Split + leakage guard
# ---------------------------------------------------------------------------

def test_temporal_split_no_shuffle(split):
    cut_ts = BASE_DATE + timedelta(days=63)
    assert all(e.day_index < 63 for e in split.train_events)
    assert all(e.day_index >= 63 for e in split.test_events)
    assert all(g["timestamp"] < cut_ts for g in split.train_gps)
    assert len(split.train_events) == 126 and len(split.test_events) == 54


def test_place_stats_frozen_to_train_window(frozen):
    """Structural leakage test: stats from the 90-day source's train window must
    equal stats from a source that has never seen days 64-90 at all (the mock's
    RNG makes day 1-63 a deterministic prefix)."""
    prefix_only = MockDataSource(n_days=63, seed=42)
    prefix_split = temporal_split(prefix_only, cut_day=63)  # all 63 days = train
    prefix_frozen = freeze_place_stats(prefix_split)

    by_key = {k: v for k, v in frozen.key_to_cluster.items()}
    prefix_by_key = {k: v for k, v in prefix_frozen.key_to_cluster.items()}
    assert set(by_key) == set(prefix_by_key)
    for key in by_key:
        a = next(p for p in frozen.places if p["cluster_id"] == by_key[key])
        b = next(p for p in prefix_frozen.places if p["cluster_id"] == prefix_by_key[key])
        assert a["visit_frequency"] == b["visit_frequency"]
        assert a["avg_stay_time"] == pytest.approx(b["avg_stay_time"], abs=0.2)


def test_labels_map_to_frozen_clusters(split, frozen):
    labels, fallback = labels_for(split.test_events, frozen)
    valid = {p["cluster_id"] for p in frozen.places}
    assert all(l in valid for l in labels)
    assert fallback == 0  # every planted place clustered in the train window


# ---------------------------------------------------------------------------
# Featurization
# ---------------------------------------------------------------------------

def test_familiarity_log_compressed(frozen):
    """The home overnight-dwell fix: familiarity must not be a home-detector."""
    norm = PlaceStatsNorm.from_places(frozen.places)
    home = next(p for p in frozen.places if p["avg_stay_time"] > 100)
    errand = next(p for p in frozen.places if p["avg_stay_time"] < 100)
    row_h = pair_row(slot_morning=True, is_weekend=False, weather_bucket="sunny",
                     current_lat=13.75, current_lng=100.50, place=home, norm=norm)
    row_e = pair_row(slot_morning=True, is_weekend=False, weather_bucket="sunny",
                     current_lat=13.75, current_lng=100.50, place=errand, norm=norm)
    i = FEATURE_NAMES.index("familiarity")
    assert row_h[i] == 1.0
    assert row_e[i] > 0.35  # raw ratio would be ~0.02 — log keeps it comparable


# ---------------------------------------------------------------------------
# Ranker: fit, predict, persist
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fitted(split, frozen):
    from app.ai.module5_recommend.evaluation import label_of
    return Module5Ranker(kind="histgbt", use_weather=True).fit(
        split.train_events, frozen.places,
        label_fn=lambda e: label_of(e, frozen),
        provenance={"data": "SIMULATED (test)"},
    )


def test_ranker_learns_from_fit(fitted, split, frozen):
    """A real fit(): the model must beat always-majority on its own training
    window (weak, deterministic check that parameters were learned from data)."""
    from app.ai.module5_recommend.evaluation import label_of, ranker_correct
    labels = [label_of(e, frozen) for e in split.train_events]
    top1, _ = ranker_correct(fitted, split.train_events, labels)
    maj = majority_cluster(frozen)
    maj_acc = np.mean([1.0 if maj == y else 0.0 for y in labels])
    assert top1.mean() > maj_acc


def test_rank_event_shape(fitted, split):
    ranking = fitted.rank_event(split.test_events[0])
    assert len(ranking) == len(fitted.places)
    scores = [s for _, s in ranking]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_score_places_unknown_weather_marginalizes(fitted):
    scores = fitted.score_places(
        fitted.places, now=datetime(2025, 4, 1, 9, 0),
        current_lat=13.75, current_lng=100.50, weather_bucket=None,
    )
    assert set(scores) == {p["cluster_id"] for p in fitted.places}
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_save_load_roundtrip(fitted, split, tmp_path):
    path = tmp_path / "ranker_patient_1.pkl"
    fitted.save(path)
    loaded = Module5Ranker.load(path)
    assert loaded.provenance["data"] == "SIMULATED (test)"
    assert loaded.rank_event(split.test_events[0]) == fitted.rank_event(split.test_events[0])


def test_load_ranker_missing_returns_none():
    assert load_ranker(999_999) is None


# ---------------------------------------------------------------------------
# Integration: score_place / generate_recommendations flags
# ---------------------------------------------------------------------------

def _ctx(frozen):
    return UserContext(
        patient_id=1, current_lat=13.75, current_lng=100.50,
        now=datetime(2025, 4, 1, 9, 0), known_places=frozen.places,
    )


def test_rules_fallback_flagged(frozen):
    scored = generate_recommendations(_ctx(frozen), ranker=None)
    assert scored and all(s.scorer == "rules" for s in scored)


def test_ml_path_flagged_and_scored(frozen, fitted):
    scored = generate_recommendations(_ctx(frozen), ranker=fitted)
    assert scored and all(s.scorer == "ml" for s in scored)
    assert all(0.0 <= s.confidence <= 1.0 for s in scored)
    # rule factors are still present as the human-readable breakdown
    assert all(set(s.factors) == {"frequency", "proximity", "familiarity", "time_match"}
               for s in scored)


def test_score_place_ml_score_override(frozen):
    place = frozen.places[0]
    s = score_place(place, _ctx(frozen), ml_score=0.42)
    assert s.scorer == "ml" and s.confidence == 0.42
