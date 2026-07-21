# pathguard/backend/app/ai/module4_search_area/search_radius_adjustment.py
"""Module 4 — Search Radius Adjustment.

Refines the base search radius calculated from Speed × Time using two signals:
  1. Familiar places density — if none exist inside the radius, expand (patient
     likely walked farther to reach a familiar spot).
  2. Wandering score (from Module 2) — high wandering → expand; low wandering
     (mid-stage, tends to stay in familiar areas) → contract.

Pure function: no DB, no side effects.
"""
from __future__ import annotations

from ._geo import haversine_m as _haversine_m

# Expansion / contraction multipliers
_EXPAND_NO_FAMILIAR = 1.50   # no known places inside radius → +50%
_EXPAND_HIGH_WANDER = 1.30   # high wandering score (≥ 0.75) → +30%
_CONTRACT_LOW_WANDER = 0.80  # low wandering score (≤ 0.30) → −20%

_HIGH_WANDER_THRESHOLD = 0.75
_LOW_WANDER_THRESHOLD = 0.30


def adjust_radius(
    base_radius_m: float,
    known_places: list[dict],
    origin_lat: float,
    origin_lng: float,
    wandering_score: float | None = None,
) -> dict:
    """Return a refined search radius and the reason for any adjustment.

    Args:
        base_radius_m:    Initial radius from Speed × Time (metres).
        known_places:     Behavioral profile places (dicts with 'latitude', 'longitude').
        origin_lat/lng:   Last known patient location.
        wandering_score:  0–1 score from Module 2 WanderingDetector (None if unavailable).

    Returns:
        {
            "adjusted_radius_m": float,
            "adjustment_reason": str,
            "_meta": {...},
        }
    """
    reasons: list[str] = []
    radius = base_radius_m

    # ── Check for familiar places within the base radius ─────────────────────
    # Use .get() with a None-guard (matching _astar_familiar and
    # identify_target_locations) so a place entry missing coordinate keys is
    # skipped rather than raising KeyError and crashing the request.
    places_in_radius = []
    for p in known_places:
        plat = p.get("latitude")
        plng = p.get("longitude")
        if plat is None or plng is None:
            continue
        if _haversine_m(origin_lat, origin_lng, float(plat), float(plng)) <= base_radius_m: 
            # ถ้าระยะห่างนั้นน้อยกว่าหรือเท่ากับรัศมีค้นหาเบื้องต้น (base_radius_m) → แปลว่าสถานที่นี้ 'อยู่ในระยะที่น่าจะไปถึงได้' ให้เพิ่มเข้า list places_in_radius
            places_in_radius.append(p)

    if not places_in_radius:
        radius *= _EXPAND_NO_FAMILIAR
        reasons.append("no familiar places in base radius → expanded 50%")

    # ── Wandering score adjustment ────────────────────────────────────────────
    if wandering_score is not None:
        if wandering_score >= _HIGH_WANDER_THRESHOLD:
            radius *= _EXPAND_HIGH_WANDER
            reasons.append(f"high wandering score ({wandering_score:.2f}) → expanded 30%")
        elif wandering_score <= _LOW_WANDER_THRESHOLD:
            radius *= _CONTRACT_LOW_WANDER
            reasons.append(f"low wandering score ({wandering_score:.2f}) → contracted 20%")

    adjustment_reason = "; ".join(reasons) if reasons else "no adjustment needed"

    return {
        "adjusted_radius_m": round(radius, 1),
        "adjustment_reason": adjustment_reason,
        "_meta": {
            "base_radius_m": base_radius_m,
            "familiar_places_in_radius": len(places_in_radius),
            "wandering_score": wandering_score,
        },
    }


if __name__ == "__main__":
    places = [
        {"latitude": 13.757, "longitude": 100.502, "visit_frequency": 5},
        {"latitude": 13.800, "longitude": 100.600, "visit_frequency": 2},  # far away
    ]
    origin = (13.7563, 100.5018)

    # Familiar place within radius → no expansion from that rule
    out = adjust_radius(1200.0, places, *origin)
    assert out["adjusted_radius_m"] == 1200.0, out
    assert out["_meta"]["familiar_places_in_radius"] == 1, out

    # No places in radius → expand 50% (use 50 m radius — nearest place is ~81 m away)
    out2 = adjust_radius(50.0, places, *origin)
    assert out2["adjusted_radius_m"] == 75.0, out2
    assert "expanded 50%" in out2["adjustment_reason"], out2

    # High wandering → expand 30%
    out3 = adjust_radius(1200.0, places, *origin, wandering_score=0.80)
    assert out3["adjusted_radius_m"] == round(1200.0 * 1.30, 1), out3

    # Low wandering → contract 20%
    out4 = adjust_radius(1200.0, places, *origin, wandering_score=0.20)
    assert out4["adjusted_radius_m"] == round(1200.0 * 0.80, 1), out4

    # No places + high wandering → both multipliers apply (50m radius, nearest place ~81m away)
    out5 = adjust_radius(50.0, places, *origin, wandering_score=0.80)
    expected = round(50.0 * _EXPAND_NO_FAMILIAR * _EXPAND_HIGH_WANDER, 1)
    assert out5["adjusted_radius_m"] == expected, (out5, expected)

    # Place entries missing/odd coordinate keys are skipped, not crashed on (Bug 3)
    messy = [
        {"name": "Home", "visit_frequency": 10},          # no coords at all
        {"lat": 13.757, "lng": 100.502},                  # wrong key names
        {"latitude": 13.757, "longitude": 100.502},       # valid, within 1200 m
    ]
    out6 = adjust_radius(1200.0, messy, *origin)
    assert out6["_meta"]["familiar_places_in_radius"] == 1, out6

    print("search_radius_adjustment: all assertions passed")
