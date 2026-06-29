"""Module 1 — place clustering (DBSCAN over GPS history)."""
import numpy as np
import pandas as pd
import pytest

from app.ai.module1_behavior.place_clustering import cluster_places

# ~1 metre in degrees of latitude near the equator/Bangkok.
M = 1.0 / 111_000.0


def _tight_cluster(center_lat, center_lng, n, start_minute, spread_m=8.0):
    """n GPS points within ~spread_m of a center, one minute apart."""
    rng = np.random.default_rng(0)
    rows = []
    base = pd.Timestamp("2026-06-01 08:00:00")
    for i in range(n):
        rows.append({
            "latitude": center_lat + rng.uniform(-spread_m, spread_m) * M,
            "longitude": center_lng + rng.uniform(-spread_m, spread_m) * M,
            "timestamp": base + pd.Timedelta(minutes=start_minute + i),
        })
    return rows


def test_cluster_places_finds_one_dense_place():
    rows = _tight_cluster(13.7500, 100.5000, n=8, start_minute=0)
    # Three far-apart isolated points -> DBSCAN noise (min_samples=5).
    rows += [
        {"latitude": 13.80, "longitude": 100.60, "timestamp": pd.Timestamp("2026-06-01 12:00:00")},
        {"latitude": 13.90, "longitude": 100.70, "timestamp": pd.Timestamp("2026-06-01 13:00:00")},
        {"latitude": 14.00, "longitude": 100.80, "timestamp": pd.Timestamp("2026-06-01 14:00:00")},
    ]
    df = pd.DataFrame(rows)

    places = cluster_places(df)

    assert len(places) == 1
    place = places[0]
    assert place["visit_frequency"] == 8
    assert place["latitude"] == pytest.approx(13.7500, abs=1e-3)
    assert place["longitude"] == pytest.approx(100.5000, abs=1e-3)
    # 8 points one minute apart, single contiguous visit -> ~7 min stay.
    assert place["avg_stay_time"] == pytest.approx(7.0, abs=0.1)


def test_cluster_places_two_separated_clusters():
    rows = _tight_cluster(13.7500, 100.5000, n=6, start_minute=0)
    rows += _tight_cluster(13.9000, 100.7000, n=6, start_minute=100)
    df = pd.DataFrame(rows)

    places = cluster_places(df)
    assert len(places) == 2
    assert {p["visit_frequency"] for p in places} == {6}


def test_cluster_places_all_noise_returns_empty():
    # Five points all far apart -> every point is noise -> no clusters.
    rows = [
        {"latitude": 13.0 + i, "longitude": 100.0 + i,
         "timestamp": pd.Timestamp("2026-06-01 08:00:00") + pd.Timedelta(hours=i)}
        for i in range(5)
    ]
    df = pd.DataFrame(rows)
    assert cluster_places(df) == []
