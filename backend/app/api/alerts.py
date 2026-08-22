# pathguard/backend/app/api/alerts.py
"""The alert feed, and the only way to say "I found her".

``alerts`` is written every scoring round a condition holds, which is right for
a history and wrong for a notification — the push cooldown in
``services/notification.py`` is what keeps the phone quiet. This router is the
other half: the caregiver opens the app and reads what actually happened, at
whatever rate it happened.

``resolved`` and ``resolved_at`` have been on the table since it was created and
nothing has ever set them. ``PATCH`` does. Resolving is deliberately reversible:
a caregiver who taps it on the wrong row in a hurry must be able to undo that,
and the column is a record of judgement, not an irreversible state machine.

Like ``tracking.py``, this is a promotion of ``scripts/demo_server.py``'s
read-only layer, not a rewrite — it always read the real table.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.database import get_db
from app.services.auth import (
    Caller, assert_may_access_patient, current_caller, verify_patient_access,
)

router = APIRouter()


class AlertOut(BaseModel):
    id: int
    patient_id: int
    alert_type: str          # geofence | emergency | gps_loss | gps_lost | wandering
    severity: str            # low | medium | high | critical
    message: str
    latitude: float | None = None
    longitude: float | None = None
    resolved: bool
    created_at: str


class AlertsOut(BaseModel):
    patient_id: int
    count: int
    alerts: list[AlertOut]


class AlertPatch(BaseModel):
    resolved: bool


def _to_out(alert) -> AlertOut:
    return AlertOut(
        id=alert.id,
        patient_id=alert.patient_id,
        alert_type=alert.alert_type,
        severity=alert.severity,
        message=alert.message,
        latitude=alert.latitude,
        longitude=alert.longitude,
        resolved=alert.resolved,
        created_at=alert.created_at.isoformat(),
    )


@router.get(
    "/api/patients/{patient_id}/alerts",
    response_model=AlertsOut,
    summary="ประวัติการแจ้งเตือนของผู้ป่วย ใหม่สุดขึ้นก่อน",
)
async def list_alerts(
    patient_id: int,
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> AlertsOut:
    if not await crud.user_exists(db, patient_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown patient_id {patient_id} — call /api/register first",
        )
    rows = await crud.get_alerts(db, patient_id, limit=limit)
    return AlertsOut(
        patient_id=patient_id,
        count=len(rows),
        alerts=[_to_out(a) for a in rows],
    )


@router.patch(
    "/api/alerts/{alert_id}",
    response_model=AlertOut,
    summary="ทำเครื่องหมายว่าจัดการแล้ว (หรือย้อนกลับ)",
)
async def patch_alert(
    alert_id: int,
    payload: AlertPatch,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> AlertOut:
    existing = await crud.get_alert(db, alert_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"alert {alert_id} not found",
        )
    # Authorize on the alert's patient, checked before anything is written.
    await assert_may_access_patient(db, caller, existing.patient_id)

    alert = await crud.set_alert_resolved(db, alert_id, payload.resolved)
    return _to_out(alert)
