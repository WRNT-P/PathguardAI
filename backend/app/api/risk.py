# pathguard/backend/app/api/risk.py
"""Module 3 endpoint — compute, persist, and alert on a patient's wandering risk.

The only Module 3 file that touches the DB. It fetches the inputs (Module 1's
behavioral profile + the patient's GPS history), runs the pure ai/ risk pipeline
(collect RAW factors -> normalize -> score -> decide), then persists a RiskScore
and any emergency / GPS-loss Alert.

Mirrors Module 5's recommendation endpoint: fetch via crud -> compute in ai/ ->
return a status-tagged response (graceful "no_data" instead of a 404).

``lat``/``lng`` query params override the "current location" used for proximity,
route-deviation and danger-zone checks; when omitted the latest stored GPS
reading is used.
"""
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud, rule_repository
from app.db.database import get_db
from app.ai.module3_risk import (
    collect_risk_factors,
    normalize_route_deviation,
    scale_wandering,
    convert_boolean,
    compute_unfamiliarity,
    calculate_risk,
    decide_emergency,
    detect_gps_gap,
)

# Module 2's mild-wandering threshold (wandering_detection._MILD_THRESHOLD):
# raw wandering_score >= 0.55 counts as "wandering detected".
_WANDERING_DETECTED_THRESHOLD = 0.55

router = APIRouter()


class RiskResponse(BaseModel):
    patient_id: int
    status: Literal["ok", "no_data"]
    message: str
    risk_score: float | None = None
    risk_level: str | None = None
    contributions: dict[str, float] | None = None
    wandering_detected: bool | None = None
    gps_available: bool | None = None
    emergency: bool = False
    reason: str | None = None


@router.get(
    "/api/risk/{patient_id}",
    response_model=RiskResponse,
    summary="Compute, persist, and alert on a patient's wandering risk score",
)
async def get_risk(
    patient_id: int,
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
) -> RiskResponse:
    # ── 0. Load the rule knowledge base (fresh per request — no cache, so a
    #       rule change in the DB affects the very next call) ──────────────────
    weights = await rule_repository.get_active_weights(db)
    thresholds = await rule_repository.get_all_thresholds(db)
    danger_zones = await rule_repository.get_active_danger_zones(db)

    # ── 1. Fetch inputs ───────────────────────────────────────────────────────
    profile = await crud.get_behavioral_profile(db, patient_id)
    gps_30d = await crud.get_gps_history(db, patient_id, days=30)
    if profile is None or not gps_30d:
        return RiskResponse(
            patient_id=patient_id,
            status="no_data",
            message="No behavioral profile or GPS history yet — cannot score risk.",
        )

    recent_gps = gps_30d[-30:]  # no last-N crud helper — slice the tail (oldest-first)

    # Current location: query override -> latest stored GPS (also used for the gap check).
    latest = await crud.get_latest_gps(db, patient_id)
    current = latest or gps_30d[-1]
    if lat is None or lng is None:
        lat, lng = current.latitude, current.longitude

    # ── 2. Adapt profile (ORM -> dict collect_risk_factors expects) ───────────
    # known_places is a JSON string; collect_risk_factors json.loads-es it itself.
    profile_dict = {"known_places": profile.known_places}

    # ── 3. RAW factors from the pure ai/ layer (KB zones passed in) ───────────
    raw = collect_risk_factors(gps_30d, recent_gps, profile_dict, lat, lng,
                               danger_zones)

    # ── 4. Normalize to the five 0–1 keys calculate_risk expects ──────────────
    normalized = {
        "route_deviation": normalize_route_deviation(
            raw["route_deviation"],
            thresholds[rule_repository.ROUTE_DEVIATION_CEILING_M]),
        "wandering": scale_wandering(raw["wandering"]),
        "confusion": raw["confusion"],  # already 0–1 — no normalizer exists
        "danger_zone": convert_boolean(raw["danger_zone"]),
        "unfamiliarity": compute_unfamiliarity(raw["familiarity"]),  # inversion lives here
    }

    # ── 5. Score (weights + level boundaries from the KB) ────────────────────
    result = calculate_risk(
        normalized, weights,
        low_ceiling=thresholds[rule_repository.LOW_CEILING],
        medium_ceiling=thresholds[rule_repository.MEDIUM_CEILING])

    # ── 6. GPS-loss check (tz-aware now to match recorded_at columns) ─────────
    gap = detect_gps_gap(last_reading=latest, now=datetime.now(timezone.utc),
                         threshold_s=thresholds[rule_repository.GPS_GAP_SECONDS])
    gps_available = not gap["gps_lost"]

    # ── 7. Derive wandering_detected (Module 2's mild threshold) ──────────────
    wandering_detected = raw["wandering"] >= _WANDERING_DETECTED_THRESHOLD

    # ── 8. Persist the risk score (contributions dict -> JSON string) ─────────
    await crud.save_risk_score(
        db,
        patient_id,
        score=result["risk_score"],
        level=result["risk_level"],
        wandering_detected=wandering_detected,
        gps_available=gps_available,
        factors=json.dumps(result["contributions"]),
    )

    # ── 9. Emergency alert — only persisted when it fires ─────────────────────
    decision = decide_emergency(result["risk_score"], raw["danger_zone"],
                                thresholds[rule_repository.EMERGENCY_SCORE])
    if decision["emergency"]:
        if decision["reason"] == "danger_zone":
            message = f"Patient entered a danger zone — risk {result['risk_score']}%."
        else:
            message = f"High risk ({result['risk_score']}%) — {decision['reason']}."
        await crud.save_alert(
            db,
            patient_id,
            alert_type=decision["alert_type"],
            severity=decision["severity"],
            message=message,
            latitude=lat,
            longitude=lng,
        )

    # ── 10. GPS-loss alert ────────────────────────────────────────────────────
    if gap["gps_lost"]:
        last_known = gap["last_known"] or {}
        await crud.save_alert(
            db,
            patient_id,
            alert_type="gps_loss",
            severity="high",
            message=f"GPS signal lost (gap {gap['gap_seconds']}s) — last known location forwarded",
            latitude=last_known.get("latitude"),
            longitude=last_known.get("longitude"),
        )

    # ── 11. Response (mirrors RecommendationResponse's status + data shape) ───
    return RiskResponse(
        patient_id=patient_id,
        status="ok",
        message=f"Risk {result['risk_score']}% ({result['risk_level']}).",
        risk_score=result["risk_score"],
        risk_level=result["risk_level"],
        contributions=result["contributions"],
        wandering_detected=wandering_detected,
        gps_available=gps_available,
        emergency=decision["emergency"],
        reason=decision["reason"],
    )
