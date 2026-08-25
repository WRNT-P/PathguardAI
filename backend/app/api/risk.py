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
from app.services.auth import Caller, verify_patient_access
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
from app.services.notification import notify_alert

# Module 2's mild-wandering threshold (wandering_detection._MILD_THRESHOLD):
# raw wandering_score >= 0.55 counts as "wandering detected".
_WANDERING_DETECTED_THRESHOLD = 0.55

# The factors that need no behavioral profile, so they work on a patient's first
# day: wandering fits on raw GPS alone (wandering_detection.py:229 states
# known_places is unused in v1) and danger_zone comes from the rule KB.
# route_deviation and unfamiliarity both require known_places
# (route_prediction.py:106-109) and are dropped until one exists.
#
# confusion was in this set until it was measured. Rule-based is not the same as
# profile-free: _rule_based_score (stop_confusion_classification.py:173-198) adds
# (1 - familiarity) * 0.20 and min(deviation / 250, 0.15), and with no known_places
# familiarity is 0.0 and deviation falls back to the 300 m default
# (risk_data_collection.py:21-26) — 0.35 of pure "we have no profile", not of
# anything the patient did. Two more sub-rules fire for anyone at rest (stopped
# 900 s +0.30, speed < 0.6 m/s +0.15), and the whole scorer only runs when
# stopped is true. Measured on 30 points: a patient sitting still at home scored
# 39.2 while one walking in circles scored 12.5 — inverted, because confusion asks
# whether a stop is abnormal and abnormality is defined by place familiarity.
# Dropping it costs no detection: both cases now score 18.8, which is honest about
# the fact that 30 points and no pins cannot tell them apart (wandering needs ~600
# points to separate them). Note the full 5-factor model is untouched — this is
# only the no-profile path, and the medical weights in risk_factor_weights are
# unchanged; _renormalize keeps wandering:danger_zone at the KB's 0.25:0.15.
_PARTIAL_FACTORS = ("wandering", "danger_zone")

# Recompute risk at most this often per patient. One /api/risk pass loads 30 days
# of GPS and fits IsolationForest + RoutePredictor, so running it on every 30 s
# reading would mean ~2,880 fits a day over a table that grows to ~86k rows.
RISK_RECOMPUTE_INTERVAL_S = 60


def _renormalize(weights: dict, keep: tuple[str, ...]) -> dict:
    """Scale a subset of the KB weights back up to sum 1.0.

    Derived from the loaded weights rather than hardcoded, so an admin editing
    ``risk_factor_weights`` can't silently desynchronise partial from full mode.
    With the seeded values (0.25/0.15) this yields 0.625/0.375.
    """
    subset = {k: weights[k] for k in keep if k in weights}
    total = sum(subset.values())
    if total <= 0:  # every usable factor disabled in the KB — nothing to score
        return {}
    return {k: v / total for k, v in subset.items()}


def _known_places(profile) -> list:
    """The patient's learned/pinned places, or [] when there is no usable profile."""
    if profile is None or not profile.known_places:
        return []
    try:
        places = json.loads(profile.known_places)
    except (json.JSONDecodeError, TypeError):
        return []
    return places if isinstance(places, list) else []

router = APIRouter()


class RiskResponse(BaseModel):
    patient_id: int
    status: Literal["ok", "partial", "no_data"]
    message: str
    # "partial" means the patient has no known_places yet, so route_deviation and
    # unfamiliarity were dropped and the remaining weights renormalized to 1.0.
    # The app must not present a partial score as if it were a full one.
    factors_used: list[str] | None = None
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


async def evaluate_risk(
    db: AsyncSession,
    patient_id: int,
    lat: float | None = None,
    lng: float | None = None,
) -> RiskResponse:
    """Score one patient now, persisting the RiskScore and any Alert.

    Extracted from the endpoint so GPS ingestion can drive it: until this was
    callable, nothing in the system ever computed risk on its own, so no alert
    could fire unless a human opened /api/risk by hand.
    """
    # ── 0. Load the rule knowledge base (fresh per request — no cache, so a
    #       rule change in the DB affects the very next call) ──────────────────
    weights = await rule_repository.get_active_weights(db)
    thresholds = await rule_repository.get_all_thresholds(db)
    danger_zones = await rule_repository.get_active_danger_zones(db)

    # ── 1. Fetch inputs ───────────────────────────────────────────────────────
    profile = await crud.get_behavioral_profile(db, patient_id)
    gps_30d = await crud.get_gps_history(db, patient_id, days=30)
    if not gps_30d:
        return RiskResponse(
            patient_id=patient_id,
            status="no_data",
            message="No GPS history yet — cannot score risk.",
        )

    # A patient with no known_places yet (day one, before Module 1 has clustered
    # anything and before the caregiver has pinned places) still gets a score,
    # from the two factors that need no profile. Dropping the other three and
    # renormalizing is not the same as leaving them in: collect_risk_factors
    # returns safety-biased defaults for them (risk_data_collection.py:21-26 —
    # familiarity 0.0, route_deviation 350 m), which alone score 31% for a
    # patient sitting still at home. Renormalizing removes them from the sum
    # instead of feeding a guess into it.
    partial = not _known_places(profile)
    if partial:
        weights = _renormalize(weights, _PARTIAL_FACTORS)
        if not weights:
            return RiskResponse(
                patient_id=patient_id,
                status="no_data",
                message="No profile yet and every profile-free factor is disabled in the rule KB.",
            )

    recent_gps = gps_30d[-30:]  # no last-N crud helper — slice the tail (oldest-first)

    # Current location: query override -> latest stored GPS (also used for the gap check).
    latest = await crud.get_latest_gps(db, patient_id)
    current = latest or gps_30d[-1]
    if lat is None or lng is None:
        lat, lng = current.latitude, current.longitude

    # ── 2. Adapt profile (ORM -> dict collect_risk_factors expects) ───────────
    # known_places is a JSON string; collect_risk_factors json.loads-es it itself.
    profile_dict = {"known_places": profile.known_places if profile else None}

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
        alert = await crud.save_alert(
            db,
            patient_id,
            alert_type=decision["alert_type"],
            severity=decision["severity"],
            message=message,
            latitude=lat,
            longitude=lng,
        )
        # Push to the caregiver. The alert row above is written every round the
        # condition holds; the cooldown that stops that becoming a push a minute
        # lives in notification.py, keyed on push_notifications.
        await notify_alert(
            db, alert, thresholds[rule_repository.PUSH_COOLDOWN_SECONDS]
        )

    # ── 10. GPS-loss alert ────────────────────────────────────────────────────
    if gap["gps_lost"]:
        last_known = gap["last_known"] or {}
        alert = await crud.save_alert(
            db,
            patient_id,
            alert_type="gps_loss",
            severity="high",
            message=f"GPS signal lost (gap {gap['gap_seconds']}s) — last known location forwarded",
            latitude=last_known.get("latitude"),
            longitude=last_known.get("longitude"),
        )
        await notify_alert(
            db, alert, thresholds[rule_repository.PUSH_COOLDOWN_SECONDS]
        )

    # ── 11. Response (mirrors RecommendationResponse's status + data shape) ───
    return RiskResponse(
        patient_id=patient_id,
        status="partial" if partial else "ok",
        message=(
            f"Risk {adj_score}% ({adj_level}) — partial score, "
            f"no known places for this patient yet."
            if partial else f"Risk {adj_score}% ({adj_level})."
        ),
        factors_used=list(weights.keys()),
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
    _: Caller = Depends(verify_patient_access),
) -> RiskResponse:
    return await evaluate_risk(db, patient_id, lat, lng)
