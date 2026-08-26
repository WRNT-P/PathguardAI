# pathguard/backend/app/api/recommendation.py
"""Module 5 endpoint — top 3 likely places for a patient, with confidence scores.

Read-only: pulls Module 1's behavioral profile and the patient's latest GPS,
scores the known places (frequency + proximity + familiarity), and returns the
top 3. Nothing is written to the DB.

``lat``/``lng`` query params override the "current location" used for proximity
(handy for testing against a seeded profile before live GPS exists). When
omitted, the latest stored GPS reading is used; if there's none, proximity is
simply dropped from the blend.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.database import get_db
from app.services.auth import Caller, verify_patient_access
from app.ai.module5_recommend import (
    build_user_context,
    generate_recommendations,
    prioritize,
)
from app.ai.module5_recommend.ranker import load_ranker
from app.models.recommendation import (
    RecommendationFlags,
    RecommendationResponse,
    RecommendedPlace,
)

router = APIRouter()


# How many places the patient's home screen shows, by stage (report, features
# table). A Level 2 patient's search box is locked, so the grid is the only way
# they can reach anywhere — it has to hold everywhere they might want to go. A
# Level 1 patient can search, so three suggestions plus a search box is enough,
# and a shorter list is less to read. Unstated stage falls back to the Level 1
# behaviour, which is what every caller got before this existed.
_TOP_N_BY_LEVEL = {1: 3, 2: 5}
_DEFAULT_TOP_N = 3


@router.get(
    "/api/recommendation/{patient_id}",
    response_model=RecommendationResponse,
    summary="สถานที่ที่น่าจะไป พร้อมคะแนนความมั่นใจ (3 อันดับ Level 1 / 5 อันดับ Level 2)",
)
async def get_recommendations(
    patient_id: int,
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> RecommendationResponse:
    profile = await crud.get_behavioral_profile(db, patient_id)

    # Current location: query override -> latest stored GPS -> none.
    if lat is None or lng is None:
        latest = await crud.get_latest_gps(db, patient_id)
        if latest is not None:
            lat, lng = latest.latitude, latest.longitude

    ctx = build_user_context(profile, lat, lng, datetime.now(timezone.utc))

    if not ctx.has_profile:
        return RecommendationResponse(
            patient_id=patient_id,
            status="no_profile",
            message="No behavioral profile yet — Module 1 has not trained this patient.",
            flags=RecommendationFlags(
                time_match_available=False, location_used=False
            ),
            recommendations=[],
        )

    # Learned ranker if one is trained for this patient; else the rule-based
    # blend, honestly flagged in the message (never passed off as ML).
    ranker = load_ranker(patient_id)
    scorer_note = (
        f"learned ranker ({ranker.provenance.get('data', 'unknown data')})"
        if ranker is not None
        else "rule-based fallback (no trained model)"
    )

    patient = await crud.get_user(db, patient_id)
    top_n = _TOP_N_BY_LEVEL.get(
        patient.severity_level if patient else None, _DEFAULT_TOP_N)
    top = prioritize(generate_recommendations(ctx, ranker=ranker), top_n=top_n)
    recommendations = [
        RecommendedPlace(
            rank=i,
            cluster_id=s.cluster_id,
            latitude=s.latitude,
            longitude=s.longitude,
            confidence=s.confidence,
            confidence_pct=round(s.confidence * 100),
            factors=s.factors,
        )
        for i, s in enumerate(top, start=1)
    ]

    return RecommendationResponse(
        patient_id=patient_id,
        status="ok",
        message=f"Top {len(recommendations)} place(s) by likelihood. Scorer: {scorer_note}.",
        flags=RecommendationFlags(
            time_match_available=False,
            location_used=ctx.has_location,
        ),
        recommendations=recommendations,
    )
