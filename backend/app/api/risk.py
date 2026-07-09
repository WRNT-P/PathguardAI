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
    apply_temporal_rules,
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
    # Temporal rules (history-based). risk_score is the FINAL (adjusted) value;
    # base_risk_score is before any temporal boost, and contributions sum to it.
    base_risk_score: float | None = None
    temporal_adjustment: float | None = None
    temporal_rules_triggered: list[str] = []


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
    base_score = result["risk_score"]

    # ── 5b. Temporal rules — use the patient's score HISTORY (trend/sustained).
    #        recent_scores are the PREVIOUS rounds (this round isn't saved yet),
    #        newest first — exactly what apply_temporal_rules expects. Additive:
    #        a patient with too little history is returned unchanged. ───────────
    temporal_rules = await rule_repository.get_active_temporal_rules(db)
    recent = await crud.get_recent_risk_scores(db, patient_id, limit=5)
    recent_scores = [r.score for r in recent]
    adj_score, adj_level, temporal_emergency, triggered = apply_temporal_rules(
        base_score, result["risk_level"], False, recent_scores, temporal_rules,
        low_ceiling=thresholds[rule_repository.LOW_CEILING],
        medium_ceiling=thresholds[rule_repository.MEDIUM_CEILING])

    # ── 6. GPS-loss check (tz-aware now to match recorded_at columns) ─────────
    gap = detect_gps_gap(last_reading=latest, now=datetime.now(timezone.utc),
                         threshold_s=thresholds[rule_repository.GPS_GAP_SECONDS])
    gps_available = not gap["gps_lost"]

    # ── 7. Derive wandering_detected (Module 2's mild threshold) ──────────────
    wandering_detected = raw["wandering"] >= _WANDERING_DETECTED_THRESHOLD

    # ── 8. Persist the ADJUSTED risk score (contributions dict -> JSON string).
    #        contributions still sum to base_score; adj_score is the headline. ──
    await crud.save_risk_score(
        db,
        patient_id,
        score=adj_score,
        level=adj_level,
        wandering_detected=wandering_detected,
        gps_available=gps_available,
        factors=json.dumps(result["contributions"]),
    )

    # ── 9. Emergency — decided on the ADJUSTED score (so a trend boost stays
    #        consistent with the saved score/level), then the sustained-risk
    #        temporal rule can force an escalation if nothing else fired. ───────
    decision = decide_emergency(adj_score, raw["danger_zone"],
                                thresholds[rule_repository.EMERGENCY_SCORE])
    if temporal_emergency and not decision["emergency"]:
        decision = {"emergency": True, "reason": "sustained_risk",
                    "severity": "high", "alert_type": "emergency"}
    if decision["emergency"]:
        if decision["reason"] == "danger_zone":
            message = f"Patient entered a danger zone — risk {adj_score}%."
        elif decision["reason"] == "sustained_risk":
            message = f"Sustained elevated risk ({adj_score}%) over recent readings — escalating."
        else:
            message = f"High risk ({adj_score}%) — {decision['reason']}."
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
        message=f"Risk {adj_score}% ({adj_level}).",
        risk_score=adj_score,
        risk_level=adj_level,
        contributions=result["contributions"],
        wandering_detected=wandering_detected,
        gps_available=gps_available,
        emergency=decision["emergency"],
        reason=decision["reason"],
        base_risk_score=base_score,
        temporal_adjustment=round(adj_score - base_score, 1),
        temporal_rules_triggered=triggered,
    )
