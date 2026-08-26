# pathguard/backend/app/api/search_area.py
"""Module 4 endpoint — Search Area Prediction for missing patients.

Activates when GPS is lost (or the caregiver manually triggers a search).
Fetches the patient's last known GPS and behavioral profile, calculates a
search radius, simulates paths via Monte Carlo + A*, and returns a probability
heatmap with three zones (high / medium / low) and familiar target locations.

Flow:
  1. Fetch profile + GPS history
  2. Extract last known position (query param overrides take precedence)
  3. Check GPS gap: if GPS is still active, return "gps_active" (no search needed)
  4. Calculate base search radius (Speed × Time)
  5. Adjust radius using behavioral data
  6. Simulate 10 000 paths (Monte Carlo + A*)
  7. Estimate probability zones via KDE
  8. Persist a gps_lost alert
  9. Return SearchAreaResponse
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud, rule_repository
from app.db.database import get_db
from app.services.auth import Caller, verify_patient_access
from app.ai.module3_risk import detect_gps_gap
from app.services.notification import notify_alert
from app.ai.module4_search_area import (
    calculate_search_radius,
    extract_last_known,
    PathSimulator,
    estimate_probability_zones,
    identify_target_locations,
    adjust_radius,
)
from app.models.search_area import (
    SearchAreaResponse,
    ProbabilityZone,
    FamiliarPath,
    TargetLocation,
    GridBounds,
)

router = APIRouter()


@router.get(
    "/api/search-area/{patient_id}",
    response_model=SearchAreaResponse,
    summary="Predict the most probable search area for a missing patient",
)
async def get_search_area(
    patient_id: int,
    last_lat: float | None = Query(None, ge=-90, le=90,
                                   description="Override last known latitude"),
    last_lng: float | None = Query(None, ge=-180, le=180,
                                   description="Override last known longitude"),
    last_speed_ms: float | None = Query(None, ge=0,
                                        description="Override last walking speed (m/s)"),
    last_direction_deg: float | None = Query(None, ge=0, lt=360,
                                             description="Override last direction (degrees)"),
    time_missing_minutes: int | None = Query(None, ge=1,
                                             description="Minutes since patient was last seen"),
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> SearchAreaResponse:

    # ── 1. Fetch inputs ───────────────────────────────────────────────────────
    # gps_history (30 days, potentially thousands of rows) is deferred until
    # after the early-exit guards below — it is only needed to fit the simulator
    # when the patient is actually missing.
    profile = await crud.get_behavioral_profile(db, patient_id)
    latest_gps = await crud.get_latest_gps(db, patient_id)

    # Need at minimum either stored GPS or caller-supplied coordinates
    has_stored_gps = latest_gps is not None
    has_override_coords = last_lat is not None and last_lng is not None

    if not has_stored_gps and not has_override_coords:
        return SearchAreaResponse(
            patient_id=patient_id,
            status="no_data",
            message="No GPS data found and no coordinates provided. Cannot predict search area.",
        )

    # ── 2. Extract last known location ────────────────────────────────────────
    last_known = extract_last_known(latest_gps)

    # Query params override stored values
    origin_lat = last_lat if last_lat is not None else last_known["lat"]
    origin_lng = last_lng if last_lng is not None else last_known["lng"]
    speed_ms = last_speed_ms if last_speed_ms is not None else last_known["speed_ms"]
    direction_deg = last_direction_deg if last_direction_deg is not None else last_known["direction_deg"]
    t_missing = time_missing_minutes if time_missing_minutes is not None else 25  # cautious default

    if origin_lat is None or origin_lng is None:
        return SearchAreaResponse(
            patient_id=patient_id,
            status="no_data",
            message="Could not determine last known coordinates.",
        )

    # ── 3. GPS gap check — is the patient actually missing? ───────────────────
    # Threshold from the rule KB — the same gps_gap_seconds row Module 3 uses,
    # so a rule change affects /api/risk and /api/search-area consistently.
    gap_threshold_s = await rule_repository.get_threshold(
        db, rule_repository.GPS_GAP_SECONDS)
    gap = detect_gps_gap(last_reading=latest_gps, now=datetime.now(timezone.utc),
                         threshold_s=gap_threshold_s)

    # If GPS is recent and no manual override, the patient is still trackable
    if not gap["gps_lost"] and not has_override_coords:
        return SearchAreaResponse(
            patient_id=patient_id,
            status="gps_active",
            message=(
                f"GPS signal is active (last update {gap['gap_seconds']:.0f}s ago). "
                "Search area prediction is not needed."
            ),
            last_known_location={
                "latitude": origin_lat,
                "longitude": origin_lng,
                "recorded_at": last_known["recorded_at"],
            },
        )

    # ── 4. Parse known places from behavioral profile ─────────────────────────
    known_places: list[dict] = []
    if profile and profile.known_places:
        try:
            raw = profile.known_places
            known_places = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(known_places, list):
                known_places = []
        except (json.JSONDecodeError, TypeError):
            known_places = []

    # ── 5. Calculate base search radius ──────────────────────────────────────
    base_radius_m = calculate_search_radius(speed_ms, t_missing)

    # ── 6. Adjust radius using behavioral data ────────────────────────────────
    # Wandering score would come from Module 2; use None when unavailable
    # Stage of illness — the report promises a narrower radius for a moderate-stage
    # patient and a wider one for an early-stage patient. None when the caregiver
    # never stated a stage, which changes nothing.
    patient = await crud.get_user(db, patient_id)
    adjustment = adjust_radius(base_radius_m, known_places, origin_lat, origin_lng,
                               wandering_score=None,
                               severity_level=patient.severity_level if patient else None)
    adjusted_radius_m = adjustment["adjusted_radius_m"]

    # ── 7. Simulate paths ─────────────────────────────────────────────────────
    # Now that we know the patient is missing, fetch the GPS history the
    # simulator needs to learn directional tendencies.
    gps_history = await crud.get_gps_history(db, patient_id, days=30)
    sim = PathSimulator(n_simulations=10_000)
    sim.fit(gps_history)
    sim_result = sim.simulate_paths(
        origin_lat, origin_lng, direction_deg, adjusted_radius_m, known_places
    )
    endpoints = sim_result.get("endpoints", [])

    # ── 8. Estimate probability zones (KDE) ───────────────────────────────────
    kde_result = estimate_probability_zones(
        endpoints, known_places, adjusted_radius_m, origin_lat, origin_lng
    )

    # ── 9. Persist GPS-loss alert ─────────────────────────────────────────────
    # Only raise the alert when GPS was actually lost. A caregiver supplying
    # override coordinates to plan a search proactively (while GPS is still
    # active) must not generate a false gps_lost alert.
    if gap["gps_lost"]:
        alert = await crud.save_alert(
            db,
            patient_id,
            alert_type="gps_lost",
            severity="high",
            message=(
                f"Search area activated — patient missing for ~{t_missing} min. "
                f"Estimated radius: {adjusted_radius_m:.0f} m."
            ),
            latitude=origin_lat,
            longitude=origin_lng,
        )
        cooldown_s = await rule_repository.get_threshold(
            db, rule_repository.PUSH_COOLDOWN_SECONDS)
        await notify_alert(db, alert, cooldown_s)

    # ── 10. Build response ────────────────────────────────────────────────────
    def _to_zones(zone_list: list[dict]) -> list[ProbabilityZone]:
        return [
            ProbabilityZone(
                latitude=z["latitude"],
                longitude=z["longitude"],
                probability=z["probability"],
            )
            for z in zone_list
        ]

    familiar_paths = [
        FamiliarPath(
            place_name=fp["place_name"],
            visit_frequency=fp["visit_frequency"],
            distance_m=fp["distance_m"],
            waypoints=fp["waypoints"],
        )
        for fp in sim_result.get("familiar_paths", [])
    ]

    target_locs = [
        TargetLocation(
            name=t["name"],
            latitude=t["latitude"],
            longitude=t["longitude"],
            visit_frequency=t["visit_frequency"],
            distance_m=t["distance_m"],
        )
        for t in kde_result.get("target_locations", [])
    ]

    grid_bounds = None
    if kde_result.get("grid_bounds"):
        gb = kde_result["grid_bounds"]
        grid_bounds = GridBounds(
            lat_min=gb["lat_min"],
            lat_max=gb["lat_max"],
            lng_min=gb["lng_min"],
            lng_max=gb["lng_max"],
        )

    return SearchAreaResponse(
        patient_id=patient_id,
        status="ok",
        message=(
            f"Search area predicted. Radius: {adjusted_radius_m:.0f} m "
            f"({len(kde_result.get('high_zone', []))} high-probability cells)."
        ),
        last_known_location={
            "latitude": origin_lat,
            "longitude": origin_lng,
            "recorded_at": last_known["recorded_at"],
        },
        search_radius_meters=base_radius_m,
        adjusted_radius_meters=adjusted_radius_m,
        adjustment_reason=adjustment["adjustment_reason"],
        high_probability_zone=_to_zones(kde_result.get("high_zone", [])),
        medium_probability_zone=_to_zones(kde_result.get("medium_zone", [])),
        low_probability_zone=_to_zones(kde_result.get("low_zone", [])),
        target_locations=target_locs,
        familiar_paths=familiar_paths,
        grid_bounds=grid_bounds,
    )
