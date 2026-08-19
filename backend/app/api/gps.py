# pathguard/backend/app/api/gps.py
"""GPS ingestion endpoints — the live path from the patient's phone.

Both endpoints hand every reading to ``gps_processor.process_gps_point``, which
owns the Kalman smoothing, the PostgreSQL write and the best-effort Firebase
live push. Nothing here touches the DB directly.

``patient_id`` is the internal int ``users.id`` returned by ``/api/register``,
not the Firebase UID string; ``recorded_at`` is UTC ISO ending in ``Z``.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.risk import RISK_RECOMPUTE_INTERVAL_S, evaluate_risk
from app.db import crud
from app.db.database import get_db
from app.models.gps_data import GPSDataCreate
from app.services import gps_processor

logger = logging.getLogger(__name__)

router = APIRouter()

# One phone queues at most a few hours of 30 s readings; cap the batch so a
# malformed client can't push an unbounded payload through the Kalman filter.
_MAX_BATCH_POINTS = 500


class GPSBatch(BaseModel):
    """Readings queued on the phone while offline, flushed in one request."""
    points: list[GPSDataCreate] = Field(..., min_length=1, max_length=_MAX_BATCH_POINTS)


class GPSAck(BaseModel):
    status: str
    patient_id: int
    accepted: int


async def _require_patient(db: AsyncSession, patient_id: int) -> None:
    """404 instead of an FK error when the app skipped ``/api/register``."""
    if not await crud.user_exists(db, patient_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown patient_id {patient_id} — call /api/register first",
        )


async def _score_risk_after_ingest(db: AsyncSession, patient_id: int) -> None:
    """Recompute risk for this patient, throttled, after their GPS landed.

    This is what makes the system notice a wander on its own. Alerts are only
    ever written while risk is being scored, and before this the only thing that
    scored risk was a human opening GET /api/risk.

    Two rules it must never break:
      * throttled — see RISK_RECOMPUTE_INTERVAL_S for why every point is too often
      * never fatal — a GPS reading that reached the database must stay there
        even if scoring blows up. Position history is the irreplaceable part;
        a score can always be recomputed from it.
    """
    try:
        latest = await crud.get_latest_risk_score(db, patient_id)
        if latest is not None:
            previous = latest.calculated_at
            if previous.tzinfo is None:  # SQLite hands back naive timestamps
                previous = previous.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - previous).total_seconds() < RISK_RECOMPUTE_INTERVAL_S:
                return
        await evaluate_risk(db, patient_id)
    except Exception as exc:  # noqa: BLE001 — never let scoring lose a GPS point
        logger.warning("Risk scoring failed for patient %s: %s", patient_id, exc)


@router.post(
    "/api/gps",
    response_model=GPSAck,
    summary="รับพิกัด GPS หนึ่งจุดจากแอปคนไข้",
)
async def receive_gps(
    reading: GPSDataCreate, db: AsyncSession = Depends(get_db)
) -> GPSAck:
    await _require_patient(db, reading.patient_id)
    await gps_processor.process_gps_point(db, reading)
    await _score_risk_after_ingest(db, reading.patient_id)
    return GPSAck(status="success", patient_id=reading.patient_id, accepted=1)


@router.post(
    "/api/gps/batch",
    response_model=GPSAck,
    summary="รับพิกัดหลายจุดรวดเดียว (คิวออฟไลน์ของแอปคนไข้)",
)
async def receive_gps_batch(
    payload: GPSBatch, db: AsyncSession = Depends(get_db)
) -> GPSAck:
    patient_ids = {p.patient_id for p in payload.points}
    if len(patient_ids) > 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="all points in a batch must belong to the same patient",
        )
    patient_id = patient_ids.pop()
    await _require_patient(db, patient_id)

    # The Kalman filter is sequential state: feeding a shuffled queue would fuse
    # readings out of order and bend the smoothed track.
    for reading in sorted(payload.points, key=lambda p: p.recorded_at):
        await gps_processor.process_gps_point(db, reading)

    # Once for the whole batch, not once per point: the score describes where the
    # patient is now, and the intermediate points are already in the history the
    # scorer reads.
    await _score_risk_after_ingest(db, patient_id)

    return GPSAck(
        status="success", patient_id=patient_id, accepted=len(payload.points)
    )
