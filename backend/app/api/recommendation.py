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
from app.ai.module5_recommend import (
    build_user_context,
    generate_recommendations,
    prioritize,
)
from app.models.recommendation import (
    RecommendationFlags,
    RecommendationResponse,
    RecommendedPlace,
)

router = APIRouter()


@router.get(
    "/api/recommendation/{patient_id}",
    response_model=RecommendationResponse,
    summary="Top 3 likely places for a patient, with confidence scores",
)
async def get_recommendations(
    patient_id: int,
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
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

    top = prioritize(generate_recommendations(ctx), top_n=3)
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
        message=f"Top {len(recommendations)} place(s) by likelihood.",
        flags=RecommendationFlags(
            time_match_available=False,
            location_used=ctx.has_location,
        ),
        recommendations=recommendations,
    )
