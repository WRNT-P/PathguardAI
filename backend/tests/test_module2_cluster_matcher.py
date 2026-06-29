"""Module 2 — cluster matcher (haversine, nearest cluster, familiarity)."""
import pytest

from app.ai.module2_prediction.cluster_matcher import (
    find_nearest_cluster,
    get_familiarity,
    haversine_km,
)

KNOWN_PLACES = [
    {"cluster_id": 0, "latitude": 13.7500, "longitude": 100.5000, "visit_frequency": 40},
    {"cluster_id": 1, "latitude": 13.7550, "longitude": 100.5050, "visit_frequency": 10},
]


def test_haversine_zero_distance():
    assert haversine_km(13.75, 100.5, 13.75, 100.5) == pytest.approx(0.0, abs=1e-9)


def test_haversine_known_distance():
    # ~1 degree of latitude ~= 111 km
    d = haversine_km(13.0, 100.0, 14.0, 100.0)
    assert d == pytest.approx(111.19, abs=0.5)


def test_haversine_symmetric():
    a = haversine_km(13.75, 100.50, 13.80, 100.55)
    b = haversine_km(13.80, 100.55, 13.75, 100.50)
    assert a == pytest.approx(b, abs=1e-12)


def test_find_nearest_within_range():
    # Right on top of cluster 0
    assert find_nearest_cluster(13.7500, 100.5000, KNOWN_PLACES) == 0


def test_find_nearest_returns_none_when_too_far():
    # Far from every known place -> unknown
    assert find_nearest_cluster(14.5, 101.5, KNOWN_PLACES) is None


def test_find_nearest_empty_places_is_none():
    assert find_nearest_cluster(13.75, 100.5, []) is None


def test_find_nearest_respects_custom_radius():
    # ~330 m due north of cluster 0 (nearest place): outside default 150 m,
    # inside a 1 km radius.
    point = (13.7530, 100.5000)
    assert find_nearest_cluster(*point, KNOWN_PLACES) is None
    assert find_nearest_cluster(*point, KNOWN_PLACES, max_distance_km=1.0) == 0


def test_familiarity_is_normalized_by_max_frequency():
    assert get_familiarity(KNOWN_PLACES, 0) == pytest.approx(1.0)   # most-visited
    assert get_familiarity(KNOWN_PLACES, 1) == pytest.approx(0.25)  # 10/40


def test_familiarity_unknown_cluster_is_zero():
    assert get_familiarity(KNOWN_PLACES, 99) == 0.0
