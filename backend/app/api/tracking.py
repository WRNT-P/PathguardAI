# pathguard/backend/app/api/tracking.py
"""The track behind the push — what the caregiver sees when they open the map.

Phase 4 gave the alert somewhere to go: a push now arrives carrying
``patient_id`` and the coordinates of the event. Nothing existed for the app to
call next. This is that call.

It is not new code. ``scripts/demo_server.py`` has served this from the real
``gps_data`` table since 2026-08-20 — "demo" was only ever a URL prefix — and
the dashboard has been drawing maps from it. Promoting it here makes it one
implementation with one contract, instead of a copy the mobile app would drift
away from.

Field names follow ``POST /api/gps`` rather than the dashboard's old shorthand,
so a client that already knows how to send a reading knows how to read one back.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.database import get_db

router = APIRouter()


async def _require_patient(db: AsyncSession, patient_id: int) -> None:
    """An unknown id is a 404, not an empty track — silence would read as
    "the patient has not moved" when it means "wrong id"."""
    if not await crud.user_exists(db, patient_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown patient_id {patient_id} — call /api/register first",
        )


class TrackPoint(BaseModel):
    latitude: float
    longitude: float
    recorded_at: str
    speed: float | None = None
    # True for points written by scripts/inject_wandering.py. Real patient data
    # is always False; the flag exists so a demo can be told apart from a person.
    synthetic_injected: bool = False


class TrackOut(BaseModel):
    patient_id: int
    count: int
    points: list[TrackPoint]


@router.get(
    "/api/patients/{patient_id}/track",
    response_model=TrackOut,
    summary="เส้นทางล่าสุดของผู้ป่วย (ไว้วาดบนแผนที่)",
)
async def get_track(
    patient_id: int,
    hours: int = Query(6, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
) -> TrackOut:
    await _require_patient(db, patient_id)
    rows = await crud.get_recent_track(db, patient_id, hours=hours)
    return TrackOut(
        patient_id=patient_id,
        count=len(rows),
        points=[
            TrackPoint(
                latitude=r.latitude,
                longitude=r.longitude,
                recorded_at=r.recorded_at.isoformat(),
                speed=r.speed,
                synthetic_injected=bool(r.synthetic_injected),
            )
            for r in rows
        ],
    )
