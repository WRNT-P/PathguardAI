# pathguard/backend/app/ai/module3_risk/risk_data_collection.py
"""Module 3.1 — Risk Data Collection (orchestrator).

Takes ALREADY-FETCHED data (never touches the DB — api/risk.py owns all I/O),
fits the Module 2 detectors, computes each risk factor, and assembles the RAW
factor dict that ``data_normalization`` (file 1) will normalize later in the api
layer. Output is intentionally pre-normalization.

Output keys (the five the formula uses, plus one alert-only signal):
  route_deviation  : float, RAW metres off the predicted route
  wandering        : float, 0–1 (from Module 2)
  confusion        : float, 0–1 (from Module 2; 0.0 when not stopped)
  danger_zone      : bool
  familiarity      : float, 0–1, RAW from get_familiarity (NOT inverted)
  outside_safe_zone: bool, patient has known places but isn't inside any of
                      them right now — NOT one of the five weighted factors,
                      consumed directly by api/risk.py to fire a standalone
                      safe_zone_exit alert (mirrors the danger-zone alert, but
                      for "left every familiar place" instead of "entered an
                      unsafe one").

The single inversion to F (unfamiliarity) lives in file 7: it calls
``compute_unfamiliarity(factors["familiarity"])`` and stores the result as
``"unfamiliarity"`` for the scoring formula. Keeping familiarity raw here means
the ``1 - familiarity`` step happens in exactly one place.

Safety-biased defaults (Decision 4 — unknown ⇒ more caution):
  - wandering detect not "ok"      → wandering   = 0.5  (neutral-cautious)
  - no predicted route, but known_places exist
                                    → route_dev   = real haversine distance to
                                      the nearest known place (not a constant —
                                      a patient far from every familiar place
                                      must score higher than one nearby)
  - no predicted route AND no known_places at all
                                    → route_dev   = 350.0 m (~0.7 after /500;
                                      truly nothing to measure against, and this
                                      case is dropped from the score anyway in
                                      partial mode — see api/risk.py)
  - stopped but classify errors    → confusion   = 0.5
  - empty / unmatched known_places → familiarity   = 0.0  (file 7 → F=1.0)
  - danger-zone check errors       → danger_zone = False (avoid false alarms)

Detectors are fit per-request (Decision 2) behind ``_prepare_detectors`` so the
fitting can be cached later without changing the orchestrator.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.ai.module2_prediction.wandering_detection import WanderingDetector
from app.ai.module2_prediction.stop_confusion_classification import StopConfusionClassifier
from app.ai.module2_prediction.route_prediction import RoutePredictor
from app.ai.module2_prediction.cluster_matcher import (
    haversine_km,
    get_familiarity,
    find_nearest_cluster,
    distance_beyond_known_places_m,
)

# ── Tunable constants ─────────────────────────────────────────────────────────
STOP_SPEED_MS = 0.3            # avg speed below this (m/s) counts as "stopped"
NO_ROUTE_DEVIATION_M = 350.0   # raw-metres default when no route can be predicted
WANDERING_DEFAULT = 0.5        # neutral-cautious wandering when detect not "ok"
CONFUSION_UNKNOWN_DEFAULT = 0.5  # stopped but classify failed
DEFAULT_STOP_DURATION_S = 60.0   # fallback when window timestamps are unusable

# Danger zones are no longer hardcoded here — they live in the rule KB
# (``danger_zones`` table, seeded by app/mock/seed_risk_rules.py with sources).
# The api/ layer loads them via rule_repository.get_active_danger_zones() and
# passes them in as circles {latitude, longitude, radius_m, name}.


# ── small extraction helpers (dependency-free, mirror Module 2) ───────────────

def _get_speed(r):
    if isinstance(r, dict):
        return r.get("speed")
    return getattr(r, "speed", None)


def _get_timestamp(r):
    if isinstance(r, dict):
        return r.get("recorded_at") or r.get("timestamp")
    return getattr(r, "recorded_at", getattr(r, "timestamp", None))


def _parse_ts(ts):
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _extract_known_places(profile: dict | None) -> list:
    """Known places a SAFETY decision may rest on — the confirmed ones only.

    Places Module 1 clustered by itself are dropped here. They are a real
    observation ("she stopped here twice") and they stay in the profile for
    prediction and for a caregiver to confirm, but until a human does confirm
    one it must not make a patient read as familiar, in-a-safe-place, or
    on-route. The failure mode that rule exists for: a patient who gets lost
    and stands in the same wrong place twice would otherwise teach the system
    that the place is safe, and the alert that should fire never would.
    """
    if not profile:
        return []
    kp = profile.get("known_places")
    if isinstance(kp, str):
        try:
            kp = json.loads(kp)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(kp, list):
        return []
    return [p for p in kp if isinstance(p, dict) and p.get("source") != "learned"]


def _avg_recent_speed(recent_gps: list, n: int = 5):
    """Mean speed (m/s) of the last ``n`` points, or None if no speed data."""
    window = recent_gps[-n:] if recent_gps else []
    valid = [s for s in (_get_speed(r) for r in window) if s is not None and s >= 0]
    return sum(valid) / len(valid) if valid else None


def _window_duration_seconds(recent_gps: list) -> float:
    """How long the recent window spans (proxy for stop duration)."""
    if not recent_gps or len(recent_gps) < 2:
        return DEFAULT_STOP_DURATION_S
    t0 = _parse_ts(_get_timestamp(recent_gps[0]))
    t1 = _parse_ts(_get_timestamp(recent_gps[-1]))
    if t0 is None or t1 is None:
        return DEFAULT_STOP_DURATION_S
    try:
        secs = (t1 - t0).total_seconds()
    except TypeError:
        return DEFAULT_STOP_DURATION_S
    return secs if secs > 0 else DEFAULT_STOP_DURATION_S


# ── danger zone (Decision 1) ──────────────────────────────────────────────────

def is_in_danger_zone(lat: float, lng: float, danger_zones: list) -> bool:
    """True if (lat, lng) falls within any of the given danger-zone circles."""
    try:
        for zone in danger_zones:
            dist_m = haversine_km(lat, lng, zone["latitude"], zone["longitude"]) * 1000.0
            if dist_m <= zone["radius_m"]:
                return True
        return False
    except Exception:
        return False  # never let a zone-check error raise a false emergency


# ── detector preparation (Decision 2: fit-per-request, cacheable later) ───────

def _prepare_detectors(gps_30d: list, known_places: list) -> dict:
    """Prepare the three Module 2 detectors.

    Wandering and route are fitted on the patient's history; confusion is a
    rule-based scorer and needs no fitting.
    """
    wandering = WanderingDetector()
    wandering.fit(gps_30d, known_places)

    confusion = StopConfusionClassifier()  # rule-based scorer; no fitting needed

    route = RoutePredictor()
    route.fit(gps_30d, known_places)

    return {"wandering": wandering, "confusion": confusion, "route": route}


# ── orchestrator ──────────────────────────────────────────────────────────────

def collect_risk_factors(
    gps_30d: list,
    recent_gps: list,
    profile: dict,
    current_lat: float,
    current_lng: float,
    danger_zones: list,
) -> dict:
    """Assemble the five RAW risk factors from fetched data (no DB, no normalize).

    ``danger_zones`` is the list of active geofence circles from the rule KB
    (loaded by the api/ layer) — this module stays DB-free.
    """
    known_places = _extract_known_places(profile)
    detectors = _prepare_detectors(gps_30d, known_places)
    defaults_fired: list[str] = []

    # ── W: wandering ──────────────────────────────────────────────────────────
    w_result = detectors["wandering"].detect(recent_gps)
    if w_result.get("status") == "ok":
        wandering = float(w_result["wandering_score"])
    else:
        wandering = WANDERING_DEFAULT
        defaults_fired.append("wandering_default")

    # ── predicted route (heuristic destination = most-visited place) ──────────
    predicted_route_tuples = None
    dest_id = None
    route_status = "skipped"
    if known_places:
        dest_id = max(known_places, key=lambda p: p.get("visit_frequency", 0))["cluster_id"]
        route_result = detectors["route"].predict_route(recent_gps, dest_id, known_places)
        route_status = route_result.get("status", "unknown")
        if route_status == "ok" and route_result.get("predicted_route"):
            predicted_route_tuples = [
                (wp["latitude"], wp["longitude"]) for wp in route_result["predicted_route"]
            ]

    # ── D: route deviation (RAW metres) ───────────────────────────────────────
    if predicted_route_tuples:
        route_deviation = min(
            haversine_km(current_lat, current_lng, wlat, wlng) * 1000.0
            for wlat, wlng in predicted_route_tuples
        )
    elif known_places:
        # No specific route could be predicted, but the patient does have a
        # profile — fall back to how far OUTSIDE their known places they are,
        # instead of a constant disconnected from reality, so "very far" and
        # "somewhat far" don't score identically. Zero while they are inside
        # one: a patient at home is not off-course, and this factor carries
        # 30 % of the score.
        route_deviation = distance_beyond_known_places_m(current_lat, current_lng, known_places)
        defaults_fired.append("route_deviation_beyond_known_places_fallback")
    else:
        route_deviation = NO_ROUTE_DEVIATION_M
        defaults_fired.append("no_route_deviation_default")

    # ── C: confusion (only meaningful while stopped — Decision 3) ─────────────
    avg_speed = _avg_recent_speed(recent_gps)
    stopped = avg_speed is not None and avg_speed < STOP_SPEED_MS
    if not stopped:
        confusion = 0.0  # not stopped → no confusion stop to classify (intentional)
    else:
        stop_duration = _window_duration_seconds(recent_gps)
        try:
            c_result = detectors["confusion"].classify(
                recent_gps,
                stop_duration,
                current_lat,
                current_lng,
                predicted_route_tuples,
                known_places,
            )
            confusion = float(c_result["confidence_score"])
        except Exception:
            confusion = CONFUSION_UNKNOWN_DEFAULT
            defaults_fired.append("confusion_unknown_default")

    # ── F-source: RAW familiarity (file 7 inverts to unfamiliarity) ───────────
    cluster_id = find_nearest_cluster(current_lat, current_lng, known_places) if known_places else None
    if cluster_id is None:
        familiarity = 0.0  # unknown place ⇒ no familiarity (file 7 → F=1.0)
        defaults_fired.append("familiarity_min")
    else:
        familiarity = get_familiarity(known_places, cluster_id)

    # ── Z: danger zone ────────────────────────────────────────────────────────
    danger_zone = is_in_danger_zone(current_lat, current_lng, danger_zones)

    # ── outside_safe_zone: patient has known places but isn't inside any of
    #    them right now. Empty known_places means no safe zone is defined yet
    #    — that must NOT read as "outside it" (would false-alarm every
    #    partial-profile patient). ────────────────────────────────────────────
    outside_safe_zone = bool(known_places) and cluster_id is None

    return {
        "route_deviation": route_deviation,
        "wandering": wandering,
        "confusion": confusion,
        "danger_zone": danger_zone,
        "familiarity": familiarity,
        "outside_safe_zone": outside_safe_zone,
        "_meta": {
            "wandering_status": w_result.get("status"),
            "route_status": route_status,
            "destination_cluster_id": dest_id,
            "stopped": stopped,
            "avg_recent_speed_ms": round(avg_speed, 3) if avg_speed is not None else None,
            "matched_cluster_id": cluster_id,
            "defaults_fired": defaults_fired,
        },
    }


if __name__ == "__main__":
    from datetime import timedelta

    NOW = datetime(2026, 6, 26, 12, 0, 0)

    PLACES = [
        {"cluster_id": 0, "latitude": 13.7460, "longitude": 100.5340,
         "visit_frequency": 40, "avg_stay_time": 120.0},   # most-visited -> dest heuristic
        {"cluster_id": 1, "latitude": 13.7510, "longitude": 100.5400,
         "visit_frequency": 12, "avg_stay_time": 30.0},
    ]

    def pt(place, ts, speed):
        return {"latitude": place["latitude"], "longitude": place["longitude"],
                "speed": speed, "recorded_at": ts}

    # Explicit zone circles (the KB seed values) — collect stays DB-free.
    ZONES = [
        {"name": "Major highway interchange (demo)",
         "latitude": 13.7700, "longitude": 100.5550, "radius_m": 150.0},
        {"name": "Canal / waterway edge (demo)",
         "latitude": 13.7400, "longitude": 100.5200, "radius_m": 200.0},
    ]

    # 30-day-ish history: alternating place0 <-> place1 across several days
    gps_30d = []
    base = datetime(2026, 6, 1, 8, 0, 0)
    for day in range(10):
        d0 = base + timedelta(days=day)
        for k in range(8):
            gps_30d.append(pt(PLACES[0], d0 + timedelta(minutes=k), 0.0))
        for k in range(8):
            gps_30d.append(pt(PLACES[1], d0 + timedelta(minutes=20 + k), 1.2))

    profile = {"known_places": PLACES}

    def approx(a, b, tol=1e-6):
        return abs(a - b) <= tol

    # 1) normal moving case near place1 -> C=0, F computed, route attempted
    recent_moving = [pt(PLACES[1], NOW - timedelta(minutes=5 - k), 1.2) for k in range(6)]
    r = collect_risk_factors(gps_30d, recent_moving, profile,
                             PLACES[1]["latitude"], PLACES[1]["longitude"], ZONES)
    assert set(r) >= {"route_deviation", "wandering", "confusion", "danger_zone", "familiarity"}, r
    assert r["confusion"] == 0.0, ("moving -> C=0", r["_meta"])
    assert 0.0 <= r["wandering"] <= 1.0, r
    assert isinstance(r["route_deviation"], float) and r["route_deviation"] >= 0.0, r
    # current is on cluster 1: RAW familiarity 12/40 = 0.3 (file 7 inverts to F=0.7)
    assert approx(r["familiarity"], 0.3), ("familiarity", r["familiarity"])
    assert r["danger_zone"] is False, r
    print("  [1] normal moving:", {k: r[k] for k in ("route_deviation", "wandering", "confusion", "familiarity")},
          "route_status=", r["_meta"]["route_status"])

    # 1b) same route, but move current ~150 m off the nearest waypoint -> D ~150 m.
    # Pure-latitude offset: distance = R*Δlat_rad, so Δlat = 150/(6371000*π/180).
    OFF_LAT = 0.001349  # ≈ 150 m north of place1
    r = collect_risk_factors(gps_30d, recent_moving, profile,
                             PLACES[1]["latitude"] + OFF_LAT, PLACES[1]["longitude"], ZONES)
    assert r["_meta"]["route_status"] == "ok", r["_meta"]
    assert 145.0 <= r["route_deviation"] <= 155.0, ("D off-route", r["route_deviation"])
    print("  [1b] 150 m off waypoint: D=", round(r["route_deviation"], 1),
          "(raw m), route_status=", r["_meta"]["route_status"])

    # 2) empty known_places -> familiarity = 0.0 (file 7 -> F=1.0) and D = 350.0
    #    (nothing to measure distance against, and outside_safe_zone stays False
    #    -- no zone is defined yet, so "outside" doesn't apply.)
    r = collect_risk_factors(gps_30d, recent_moving, {"known_places": []},
                             PLACES[1]["latitude"], PLACES[1]["longitude"], ZONES)
    assert r["familiarity"] == 0.0, r
    assert r["route_deviation"] == NO_ROUTE_DEVIATION_M, r
    assert r["outside_safe_zone"] is False, r
    print("  [2] empty known_places: familiarity=", r["familiarity"], "D=", r["route_deviation"],
          "outside_safe_zone=", r["outside_safe_zone"])

    # 3) no recent_gps -> route can't be predicted, but known_places exist ->
    #    D falls back to how far outside the known places they are, not the
    #    350.0 constant. Standing on place1 itself, that is 0.
    r = collect_risk_factors(gps_30d, [], profile,
                             PLACES[1]["latitude"], PLACES[1]["longitude"], ZONES)
    assert approx(r["route_deviation"], 0.0, tol=1.0), r
    assert "route_deviation_beyond_known_places_fallback" in r["_meta"]["defaults_fired"], r["_meta"]
    print("  [3] no recent_gps: D=", r["route_deviation"], "defaults=", r["_meta"]["defaults_fired"])

    # 3c) inside a known place but off its centre -> still 0, because the pin's
    #     radius is what "at this place" means. Regression for a patient scored
    #     both at home (F=0, C=0) and 279 m off-course at the same instant.
    WIDE = [{**PLACES[1], "radius_m": 400}]
    r = collect_risk_factors(gps_30d, [], {"known_places": WIDE},
                             PLACES[1]["latitude"] + 0.0025, PLACES[1]["longitude"], ZONES)
    assert r["route_deviation"] == 0.0, ("inside a 400 m pin is not off-route", r["route_deviation"])
    assert r["outside_safe_zone"] is False, r
    print("  [3c] 278 m inside a 400 m pin: D=", r["route_deviation"],
          "outside_safe_zone=", r["outside_safe_zone"])

    # 3b) same as [3], but far from every known place -> D = real large
    #     distance (not the flat 350.0 constant) and outside_safe_zone = True.
    FAR_LAT, FAR_LNG = 14.5000, 101.5000  # well outside every place's radius
    r = collect_risk_factors(gps_30d, [], profile, FAR_LAT, FAR_LNG, ZONES)
    assert r["route_deviation"] > 10_000.0, ("far D should be large, not 350.0", r["route_deviation"])
    assert r["outside_safe_zone"] is True, r
    print("  [3b] far from every known place: D=", round(r["route_deviation"], 1),
          "outside_safe_zone=", r["outside_safe_zone"])

    # 4) stopped case (avg speed < 0.3) -> classify path runs, C in [0,1]
    recent_stopped = [pt(PLACES[1], NOW - timedelta(minutes=10 - k), 0.05) for k in range(8)]
    r = collect_risk_factors(gps_30d, recent_stopped, profile,
                             PLACES[1]["latitude"], PLACES[1]["longitude"], ZONES)
    assert r["_meta"]["stopped"] is True, r["_meta"]
    assert 0.0 <= r["confusion"] <= 1.0, r
    print("  [4] stopped: C=", r["confusion"], "stopped=", r["_meta"]["stopped"])

    # 5) danger zone: a point inside a zone -> True; a far point -> False
    z = ZONES[0]
    assert is_in_danger_zone(z["latitude"], z["longitude"], ZONES) is True
    assert is_in_danger_zone(13.7000, 100.4000, ZONES) is False
    r_in = collect_risk_factors(gps_30d, recent_moving, profile,
                                z["latitude"], z["longitude"], ZONES)
    assert r_in["danger_zone"] is True, r_in
    print("  [5] danger zone: center=True, far=False, collect.danger_zone=", r_in["danger_zone"])

    print("risk_data_collection: all assertions passed")
