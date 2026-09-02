"""Module 1 — place clustering (stay-point extraction + DBSCAN over stops).

Feeding raw GPS fixes straight into DBSCAN (the old approach) counted "how many
fixes landed near here", which a dense 20s-interval track satisfies at a red
light just as easily as at a home. These tests build tracks with realistic
*stays* (>=15 continuous minutes near one spot) instead of dense point clouds,
matching what production data actually looks like.
"""
import numpy as np
import pandas as pd
import pytest

from app.ai.module1_behavior.place_clustering import cluster_places

# ~1 metre in degrees of latitude near the equator/Bangkok.
M = 1.0 / 111_000.0


def _stay(center_lat, center_lng, start, duration_minutes=20, step_minutes=5, spread_m=8.0, seed=0):
    """Fixes near a center, `step_minutes` apart, covering `duration_minutes` —
    one real stay, the unit `extract_stay_points` is meant to find."""
    rng = np.random.default_rng(seed)
    n = int(duration_minutes / step_minutes) + 1
    return [
        {
            "latitude": center_lat + rng.uniform(-spread_m, spread_m) * M,
            "longitude": center_lng + rng.uniform(-spread_m, spread_m) * M,
            "timestamp": start + pd.Timedelta(minutes=i * step_minutes),
        }
        for i in range(n)
    ]


def _passing_by(lat, lng, ts):
    """A single isolated fix — never long enough to be a stay on its own, but
    breaks the position-continuity between two real stays in the test data."""
    return {"latitude": lat, "longitude": lng, "timestamp": ts}


def test_cluster_places_finds_one_dense_place():
    rows = _stay(13.7500, 100.5000, pd.Timestamp("2026-06-01 08:00:00"))
    rows.append(_passing_by(13.80, 100.60, pd.Timestamp("2026-06-01 09:00:00")))
    rows += _stay(13.7500, 100.5000, pd.Timestamp("2026-06-02 08:00:00"), seed=1)
    rows.append(_passing_by(13.90, 100.70, pd.Timestamp("2026-06-02 09:30:00")))
    df = pd.DataFrame(rows)

    places = cluster_places(df)

    assert len(places) == 1
    place = places[0]
    assert place["visit_frequency"] == 2  # two stays, not the raw fix count
    assert place["latitude"] == pytest.approx(13.7500, abs=1e-3)
    assert place["longitude"] == pytest.approx(100.5000, abs=1e-3)
    assert place["avg_stay_time"] == pytest.approx(20.0, abs=0.5)


def test_cluster_places_two_separated_clusters():
    day1 = pd.Timestamp("2026-06-01 08:00:00")
    day2 = pd.Timestamp("2026-06-02 08:00:00")
    rows = _stay(13.7500, 100.5000, day1, seed=0)
    rows += _stay(13.9000, 100.7000, day1 + pd.Timedelta(hours=5), seed=1)
    rows += _stay(13.7500, 100.5000, day2, seed=2)
    rows += _stay(13.9000, 100.7000, day2 + pd.Timedelta(hours=5), seed=3)
    df = pd.DataFrame(rows)

    places = cluster_places(df)
    assert len(places) == 2
    assert {p["visit_frequency"] for p in places} == {2}


def test_cluster_places_all_noise_returns_empty():
    # Five isolated points, an hour apart, nowhere near each other — no run of
    # fixes ever stays put long enough to become a stay point.
    rows = [
        {"latitude": 13.0 + i, "longitude": 100.0 + i,
         "timestamp": pd.Timestamp("2026-06-01 08:00:00") + pd.Timedelta(hours=i)}
        for i in range(5)
    ]
    df = pd.DataFrame(rows)
    assert cluster_places(df) == []


def test_cluster_places_a_single_brief_stop_is_not_a_place():
    """A 3-minute stop (a red light, a quick stop) must not surface as a
    'known place' — this is the exact failure mode the old raw-fix DBSCAN had."""
    rows = _stay(13.7500, 100.5000, pd.Timestamp("2026-06-01 08:00:00"),
                 duration_minutes=3, step_minutes=1)
    df = pd.DataFrame(rows)
    assert cluster_places(df) == []
