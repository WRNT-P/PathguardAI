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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.database import get_db
from app.services.notification import notify_claim
from app.services.auth import (
    Caller, assert_may_access_patient, current_caller, verify_patient_access,
)

router = APIRouter()


class AlertOut(BaseModel):
    id: int
    patient_id: int
    alert_type: str          # geofence | emergency | gps_loss | wandering | sos | trip_denied
    severity: str            # low | medium | high | critical
    message: str
    latitude: float | None = None
    longitude: float | None = None
    resolved: bool
    claimed_by: int | None = None
    claimed_by_name: str | None = None
    claimed_at: str | None = None
    # Where the caregiver who claimed this was, last time their app reported.
    # The app side asked for it so the family can watch somebody actually
    # approach rather than read a name and wonder. Only ever the claimer's
    # position, only while the claim stands — this is not a caregiver map.
    claimed_by_latitude: float | None = None
    claimed_by_longitude: float | None = None
    claimed_by_location_age_s: float | None = Field(
        None,
        description="ตำแหน่งของคนที่กดรับเก่ากี่วินาที · null = ไม่เคยส่งตำแหน่งเลย")
    created_at: str


class AlertsOut(BaseModel):
    patient_id: int
    count: int
    alerts: list[AlertOut]


class AlertPatch(BaseModel):
    resolved: bool


def _to_out(alert, claimer=None, now: datetime | None = None) -> AlertOut:
    """Render one alert, filling in whoever claimed it if the row was loaded.

    Takes the caregiver *row* rather than their name, because the app needs
    three things about them and passing a name meant the other two could not be
    reached. It also fixes a hole this signature caused: the list and the PATCH
    called it with no name at all, so every claimed alert the caregiver's app
    listed came back ``claimed_by: 5, claimed_by_name: null`` — an id where a
    person's name belongs, on the one screen that exists to say who is going.
    """
    return AlertOut(
        id=alert.id,
        patient_id=alert.patient_id,
        alert_type=alert.alert_type,
        severity=alert.severity,
        message=alert.message,
        latitude=alert.latitude,
        longitude=alert.longitude,
        resolved=alert.resolved,
        claimed_by=alert.claimed_by,
        claimed_by_name=None if claimer is None else claimer.name,
        claimed_at=None if alert.claimed_at is None else alert.claimed_at.isoformat(),
        claimed_by_latitude=None if claimer is None else claimer.last_latitude,
        claimed_by_longitude=None if claimer is None else claimer.last_longitude,
        claimed_by_location_age_s=crud.location_age_seconds(
            claimer, now or datetime.now(timezone.utc)),
        created_at=alert.created_at.isoformat(),
    )


async def _claimers(db: AsyncSession, alerts) -> dict[int, object]:
    """Load every distinct caregiver named by ``alerts``, once each.

    Most alerts are unclaimed and a claimed one is usually claimed by the same
    person, so this is a couple of rows per page, not one per alert.
    """
    ids = {a.claimed_by for a in alerts if a.claimed_by is not None}
    loaded = {}
    for user_id in ids:
        user = await crud.get_user(db, user_id)
        if user is not None:
            loaded[user_id] = user
    return loaded


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
    claimers = await _claimers(db, rows)
    now = datetime.now(timezone.utc)
    return AlertsOut(
        patient_id=patient_id,
        count=len(rows),
        alerts=[_to_out(a, claimers.get(a.claimed_by), now) for a in rows],
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
    # Resolving does not clear the claim, so the answer must still carry it.
    claimer = (None if alert.claimed_by is None
               else await crud.get_user(db, alert.claimed_by))
    return _to_out(alert, claimer)


# ── "I'll go and get them" (report C-2) ───────────────────────────────────────

class ClaimOut(BaseModel):
    alert: AlertOut
    push: str = Field(
        ..., description="ผลการแจ้งผู้ดูแลคนอื่น: sent | no_other_caregiver | failed | error")


@router.post(
    "/api/alerts/{alert_id}/claim",
    response_model=ClaimOut,
    summary="รับเรื่องเอง — บอกผู้ดูแลคนอื่นว่ากำลังไปรับผู้ป่วย",
)
async def claim_alert(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> ClaimOut:
    """Take responsibility for one alert, and tell the other caregivers.

    **409 when somebody else already holds it, and the body names them.** The
    caregiver who lost the race is about to decide whether to set off anyway, so
    "taken" without a name is not an answer they can act on — and two people
    driving to the same place while a third assumes it is handled is the failure
    this whole feature exists to prevent.

    Claiming is **not** resolving. Resolved means the patient is safe; claimed
    means somebody is on their way. An alert that closed itself when a caregiver
    said "I'm going" would erase the row that says the situation is still open.

    The push goes to everyone *except* the claimer and carries no cooldown — see
    ``notify_claim``.
    """
    existing = await crud.get_alert(db, alert_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"alert {alert_id} not found",
        )
    await assert_may_access_patient(db, caller, existing.patient_id)

    if caller.user_id is None:
        # Auth off: there is no "who". Recording a claim by nobody would show
        # the family that somebody is going when nothing was decided.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="claiming an alert requires a signed-in caregiver",
        )

    if existing.claimed_by is not None and existing.claimed_by != caller.user_id:
        holder = await crud.get_user(db, existing.claimed_by)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "alert already claimed",
                "claimed_by": existing.claimed_by,
                "claimed_by_name": holder.name if holder else None,
                "claimed_at": (None if existing.claimed_at is None
                               else existing.claimed_at.isoformat()),
            },
        )

    already_mine = existing.claimed_by == caller.user_id
    alert = await crud.claim_alert(db, alert_id, caller.user_id)
    me = await crud.get_user(db, caller.user_id)
    name = me.name if me else str(caller.user_id)

    # Re-claiming something you already hold is a duplicate tap, not news. The
    # others were told the first time; telling them again on every tap is how a
    # notification channel gets muted.
    push_status = "duplicate" if already_mine else (
        await notify_claim(db, alert, name))["status"]

    return ClaimOut(alert=_to_out(alert, me), push=push_status)


@router.delete(
    "/api/alerts/{alert_id}/claim",
    response_model=AlertOut,
    summary="ยกเลิกการรับเรื่อง — ไปไม่ได้แล้ว ให้คนอื่นรับแทน",
)
async def release_alert(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> AlertOut:
    """Give a claimed alert back so somebody else can take it.

    This exists because the alternative is worse than an extra endpoint: a
    caregiver who says "I'm going" and then cannot — the car will not start,
    they are further away than they thought — would otherwise leave an alert
    that reads as handled while nobody is on their way. That is the exact state
    the claim was introduced to make impossible.

    **Only the holder may release**, and releasing does not notify: the point of
    releasing is that this person is not going, and the caregiver who picks it up
    next will send the notification that matters when they claim it.
    """
    existing = await crud.get_alert(db, alert_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"alert {alert_id} not found",
        )
    await assert_may_access_patient(db, caller, existing.patient_id)

    if existing.claimed_by is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="alert is not claimed",
        )
    if caller.authenticated and existing.claimed_by != caller.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the caregiver who claimed this alert may release it",
        )

    return _to_out(await crud.release_alert(db, alert_id))
