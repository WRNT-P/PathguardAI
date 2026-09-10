# pathguard/backend/app/api/trip_events.py
"""Patient-app-reported trip lifecycle events — started, arrived, off-route.

Unlike everything else in ``alerts``, these three are never derived from a
recomputed risk score: the patient's own phone is the only thing that knows
when a walk began, when it ended at the destination, and (per the on-device
route the Directions API drew) how far it has strayed from that route right
now. The server has none of that geometry, so it cannot re-derive or
auto-resolve any of these — same reasoning as ``sos``.

``started``/``arrived`` are written already resolved: neither is an ongoing
condition a caregiver has to close, they're a feed entry. ``off_route`` is
left unresolved like ``sos`` — a caregiver dismisses it once they know the
patient is back on track.
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud, rule_repository
from app.db.database import get_db
from app.services.auth import Caller, verify_patient_access
from app.services.notification import notify_alert

logger = logging.getLogger(__name__)

router = APIRouter()

_ALERT_TYPE_FOR_EVENT = {
    "started": "trip_started",
    "arrived": "trip_arrived",
    "off_route": "off_route",
}

# Only "off_route" is actionable (a caregiver may want to check in); the other
# two are a feed entry with nothing to resolve.
_AUTO_RESOLVED_EVENTS = {"started", "arrived"}


class TripEventIn(BaseModel):
    event: Literal["started", "arrived", "off_route"]
    destination_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None


def _message(event: str, destination_name: str | None) -> str:
    if event == "started":
        return (
            f"Started walking to {destination_name}."
            if destination_name
            else "Started a trip."
        )
    if event == "arrived":
        return (
            f"Arrived at {destination_name}."
            if destination_name
            else "Arrived at their destination."
        )
    return "Went off the planned route."


@router.post(
    "/api/patients/{patient_id}/trip-events",
    status_code=status.HTTP_201_CREATED,
    summary="Patient app reports it started a trip, arrived, or went off-route",
)
async def report_trip_event(
    patient_id: int,
    payload: TripEventIn,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> dict:
    if not await crud.user_exists(db, patient_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown patient_id {patient_id} — call /api/register first",
        )

    alert = await crud.save_alert(
        db,
        patient_id,
        alert_type=_ALERT_TYPE_FOR_EVENT[payload.event],
        # "off_route" gets its own severity band (medium) so it reads as more
        # than the plain feed entries but doesn't compete visually with a real
        # emergency/geofence row.
        severity="low" if payload.event in _AUTO_RESOLVED_EVENTS else "medium",
        message=_message(payload.event, payload.destination_name),
        latitude=payload.latitude,
        longitude=payload.longitude,
        destination_name=payload.destination_name,
    )

    if payload.event in _AUTO_RESOLVED_EVENTS:
        await crud.set_alert_resolved(db, alert.id, True)
    else:
        thresholds = await rule_repository.get_all_thresholds(db)
        await notify_alert(
            db, alert, thresholds[rule_repository.PUSH_COOLDOWN_SECONDS]
        )

    return {"id": alert.id, "alert_type": alert.alert_type}
