# pathguard/backend/app/ai/module4_search_area/probability_area_estimation.py
"""Module 4.3 — Probability Area Estimation.

Applies Kernel Density Estimation (KDE) to the simulated path endpoints from
Step 4.2 and produces a probability heatmap divided into three zones:
  - High   (≥ 70th percentile of KDE density)
  - Medium (40th–70th percentile)
  - Low    (< 40th percentile)

Also identifies familiar target locations within the search radius for
highlighting on the caregiver map.

Pure functions: no DB, no side effects.
"""
from __future__ import annotations

import math

try:
    import numpy as np
    from scipy.stats import gaussian_kde  # type: ignore[import]
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

from ._geo import EARTH_RADIUS_KM, haversine_m as _haversine_m

_GRID_STEPS = 50          # 50×50 = 2500 grid cells


def _radius_to_deg(radius_m: float, origin_lat: float) -> tuple[float, float]:
    """Convert metres radius to degree offsets for lat and lng."""
    lat_delta = radius_m / 111_320.0
    lng_delta = radius_m / (111_320.0 * math.cos(math.radians(origin_lat)))
    return lat_delta, lng_delta


def _fallback_zones(
    endpoints: list,
    origin_lat: float,
    origin_lng: float,
    search_radius_m: float,
) -> tuple[list, list, list]:
    """Pure-Python fallback when scipy is unavailable.

    Classifies endpoints by distance: closest third = high, middle = medium,
    farthest third = low.
    """
    if not endpoints:
        return [], [], []

    pts_with_dist = []
    for ep in endpoints:
        lat, lng = ep[0], ep[1]
        dist = _haversine_m(origin_lat, origin_lng, lat, lng)
        pts_with_dist.append((dist, lat, lng))
    pts_with_dist.sort(key=lambda x: x[0])
    max_dist = pts_with_dist[-1][0] or 1.0  # avoid divide-by-zero if all coincident
    n = len(pts_with_dist)
    t1, t2 = n // 3, 2 * n // 3

    def _cell(p):
        # Closer to origin → higher probability (0–1), matching the dict shape
        # _kde_zones produces so the router's _to_zones can read the same keys.
        prob = 1.0 - (p[0] / max_dist)
        return {"latitude": round(p[1], 6),
                "longitude": round(p[2], 6),
                "probability": round(prob, 4)}

    high = [_cell(p) for p in pts_with_dist[:t1]]
    medium = [_cell(p) for p in pts_with_dist[t1:t2]]
    low = [_cell(p) for p in pts_with_dist[t2:]]
    return high, medium, low


def estimate_probability_zones(
    endpoints: list,
    known_places: list[dict],
    search_radius_m: float,
    origin_lat: float,
    origin_lng: float,
) -> dict:
    """Apply KDE to simulated endpoints and return three probability zones.

    Each zone is a list of {"latitude": float, "longitude": float, "probability": float}.
    The "probability" is the normalised KDE density (0–1) at that grid cell.

    Returns:
        {
            "status": "ok",
            "high_zone": [...],
            "medium_zone": [...],
            "low_zone": [...],
            "target_locations": [...],
            "grid_bounds": {"lat_min", "lat_max", "lng_min", "lng_max"},
            "_meta": {...},
        }
    """
    target_locations = identify_target_locations(known_places, origin_lat, origin_lng, search_radius_m)

    if not endpoints:
        return {
            "status": "no_endpoints",
            "high_zone": [],
            "medium_zone": [],
            "low_zone": [],
            "target_locations": target_locations,
            "grid_bounds": None,
            "_meta": {"endpoint_count": 0},
        }

    lat_delta, lng_delta = _radius_to_deg(search_radius_m, origin_lat)
    lat_min = origin_lat - lat_delta
    lat_max = origin_lat + lat_delta
    lng_min = origin_lng - lng_delta
    lng_max = origin_lng + lng_delta

    if _HAS_SCIPY:
        high, medium, low, method = _kde_zones(
            endpoints, lat_min, lat_max, lng_min, lng_max
        )
    else:
        high, medium, low = _fallback_zones(endpoints, origin_lat, origin_lng, search_radius_m)
        method = "fallback_distance"

    return {
        "status": "ok",
        "high_zone": high,
        "medium_zone": medium,
        "low_zone": low,
        "target_locations": target_locations,
        "grid_bounds": {
            "lat_min": lat_min,
            "lat_max": lat_max,
            "lng_min": lng_min,
            "lng_max": lng_max,
        },
        "_meta": {
            "endpoint_count": len(endpoints),
            "grid_steps": _GRID_STEPS,
            "method": method,
            "high_count": len(high),
            "medium_count": len(medium),
            "low_count": len(low),
        },
    }


def _kde_zones(
    endpoints: list,
    lat_min: float, lat_max: float,
    lng_min: float, lng_max: float,
) -> tuple[list, list, list, str]:
    """Run scipy gaussian_kde on a 50×50 grid; classify by percentile."""
    lats = np.array([ep[0] for ep in endpoints])
    lngs = np.array([ep[1] for ep in endpoints])

    try:
        kde = gaussian_kde(np.vstack([lats, lngs]))
    except Exception:
        # Degenerate dataset (e.g. all identical points)
        high, medium, low = _fallback_zones(endpoints, (lat_min + lat_max) / 2,
                                            (lng_min + lng_max) / 2,
                                            (lat_max - lat_min) * 111320 / 2)
        return high, medium, low, "fallback_degenerate"

    grid_lats = np.linspace(lat_min, lat_max, _GRID_STEPS)
    grid_lngs = np.linspace(lng_min, lng_max, _GRID_STEPS)
    ll, gg = np.meshgrid(grid_lats, grid_lngs)
    grid_pts = np.vstack([ll.ravel(), gg.ravel()])

    density = kde(grid_pts)
    max_d = density.max()
    if max_d > 0:
        density = density / max_d  # normalise 0–1

    p70 = float(np.percentile(density, 70))
    p40 = float(np.percentile(density, 40))

    high: list[dict] = []
    medium: list[dict] = []
    low: list[dict] = []

    for i, (lat_val, lng_val, prob) in enumerate(
        zip(ll.ravel(), gg.ravel(), density)
    ):
        cell = {"latitude": round(float(lat_val), 6),
                "longitude": round(float(lng_val), 6),
                "probability": round(float(prob), 4)}
        if prob >= p70:
            high.append(cell)
        elif prob >= p40:
            medium.append(cell)
        else:
            low.append(cell)

    return high, medium, low, "gaussian_kde"


def identify_target_locations(
    known_places: list[dict],
    origin_lat: float,
    origin_lng: float,
    radius_m: float,
) -> list[dict]:
    """Return familiar places within radius_m, sorted by visit frequency."""
    targets = []
    for p in known_places:
        plat = p.get("latitude")
        plng = p.get("longitude")
        if plat is None or plng is None:
            continue
        dist = _haversine_m(origin_lat, origin_lng, float(plat), float(plng))
        if dist <= radius_m:
            targets.append({
                "name": p.get("place_name", p.get("name", "unknown")),
                "latitude": float(plat),
                "longitude": float(plng),
                "visit_frequency": p.get("visit_frequency", 0),
                "distance_m": round(dist, 1),
            })
    targets.sort(key=lambda x: x["visit_frequency"], reverse=True)
    return targets


if __name__ == "__main__":
    import random
    rng = random.Random(0)
    origin = (13.7563, 100.5018)

    # Generate synthetic endpoints around origin
    endpoints = [
        [origin[0] + rng.uniform(-0.005, 0.005),
         origin[1] + rng.uniform(-0.005, 0.005)]
        for _ in range(500)
    ]

    places = [
        {"latitude": 13.757, "longitude": 100.502, "visit_frequency": 10, "place_name": "Park"},
        {"latitude": 13.900, "longitude": 100.700, "visit_frequency": 3, "place_name": "Far"},
    ]

    result = estimate_probability_zones(endpoints, places, 1200.0, *origin)
    assert result["status"] == "ok", result
    assert len(result["high_zone"]) > 0
    assert len(result["medium_zone"]) > 0
    assert len(result["low_zone"]) > 0
    assert len(result["target_locations"]) == 1  # only "Park" within 1200m
    assert result["target_locations"][0]["name"] == "Park"

    # Empty endpoints
    result2 = estimate_probability_zones([], places, 1200.0, *origin)
    assert result2["status"] == "no_endpoints"
    assert result2["high_zone"] == []

    # identify_target_locations
    targets = identify_target_locations(places, *origin, 1200.0)
    assert len(targets) == 1
    assert targets[0]["name"] == "Park"

    # Fallback zones must emit the same dict shape as the KDE path so the
    # router's _to_zones can read latitude/longitude/probability keys (Bug 1)
    fb_high, fb_med, fb_low = _fallback_zones(endpoints, *origin, 1200.0)
    for cell in (fb_high + fb_med + fb_low):
        assert set(cell) == {"latitude", "longitude", "probability"}, cell
        assert 0.0 <= cell["probability"] <= 1.0, cell

    print("probability_area_estimation: all assertions passed")
