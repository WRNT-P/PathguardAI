# pathguard/backend/app/api/sos.py
"""ปุ่ม SOS — the one alert in this system that nothing has to guess at.

Every other path to a caregiver's phone is inference: a risk score crosses a
threshold, a geofence trips, GPS goes quiet. This one is the opposite. A person
pressed a button, and there is nothing left to decide.

**Why this is not a flag on POST /api/gps**, which is where it was nearly put.
Two reasons, both fatal:

1. GPS ingest recomputes risk at most once every 60 s per patient
   (``risk.RISK_RECOMPUTE_INTERVAL_S``). An SOS arriving 20 s after the last
   pass would be stored and scored by nobody — the button would do nothing, and
   nothing would say so.
2. Even when it is scored, whether an alert is raised is ``decide_emergency``'s
   call. A patient pressing the button while standing in their own living room
   scores about 16.5, which is ``low``, which raises nothing. The button would
   be silent in the place the patient spends most of their time.

So this path skips scoring entirely: write the alert, push it, answer. The
patient is the authority on whether the patient needs help.

**Failure policy.** Nothing here may 500 for a reason that is not "the database
is gone". The alert row is written first and the push is attempted second,
because ``alerts`` is what the dashboard reads — if FCM is unreachable, the
caregiver watching the dashboard still sees the press. ``notify_alert`` never
raises by design, and a missing rule-KB row degrades to a default rather than
taking the endpoint down with it.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud, rule_repository
from app.db.database import get_db
from app.services.auth import Caller, assert_may_access_patient, current_caller
from app.services.notification import notify_alert

logger = logging.getLogger(__name__)

router = APIRouter()

# Used only if the rule KB has no sos_cooldown_seconds row — i.e. the DB was
# never re-seeded after this endpoint shipped. Matches the seeded value so
# behaviour does not change when the row appears; see seed_risk_rules.py for why
# it is 60 s and not 0.
_FALLBACK_COOLDOWN_S = 60.0

_MESSAGE = "Patient pressed the SOS button."


class SOSIn(BaseModel):
    patient_id: int = Field(..., description="users.id ของผู้ป่วย (จาก /api/register)")
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class SOSOut(BaseModel):
    alert_id: int
    patient_id: int
    severity: str
    latitude: float | None
    longitude: float | None
    push: str  # sent | cooldown | no_caregiver | failed | error


async def _cooldown_seconds(db: AsyncSession) -> float:
    try:
        return await rule_repository.get_threshold(
            db, rule_repository.SOS_COOLDOWN_SECONDS
        )
    except Exception:
        logger.warning(
            "rule KB has no %s — falling back to %.0fs. Run: "
            "python -m app.mock.seed_risk_rules",
            rule_repository.SOS_COOLDOWN_SECONDS, _FALLBACK_COOLDOWN_S,
        )
        return _FALLBACK_COOLDOWN_S


@router.post(
    "/api/sos",
    response_model=SOSOut,
    status_code=status.HTTP_201_CREATED,
    summary="ผู้ป่วยกดปุ่มขอความช่วยเหลือ — แจ้งผู้ดูแลทันที ไม่ผ่านการให้คะแนน",
)
async def raise_sos(
    payload: SOSIn,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> SOSOut:
    await assert_may_access_patient(db, caller, payload.patient_id)
    if not await crud.user_exists(db, payload.patient_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown patient_id {payload.patient_id} — call /api/register first",
        )

    # The phone knows where it is right now, so the body wins. Falling back to
    # the last stored fix means an SOS pressed with the GPS chip cold still
    # carries somewhere to look, which is the whole point of the alert.
    lat, lng = payload.latitude, payload.longitude
    if lat is None or lng is None:
        latest = await crud.get_latest_gps(db, payload.patient_id)
        if latest is not None:
            lat, lng = latest.latitude, latest.longitude

    alert = await crud.save_alert(
        db,
        payload.patient_id,
        alert_type="sos",
        # Its own alert_type, not "emergency". The push cooldown is keyed on
        # (patient_id, alert_type), so sharing that key would let an automatic
        # emergency from three minutes ago silently swallow the patient's press.
        severity="critical",
        message=_MESSAGE,
        latitude=lat,
        longitude=lng,
    )

    push = await notify_alert(db, alert, await _cooldown_seconds(db))
    if push["status"] != "sent":
        logger.warning(
            "SOS for patient=%s was not pushed (%s) — alert %s is stored and "
            "visible on the dashboard",
            payload.patient_id, push["status"], alert.id,
        )

    return SOSOut(
        alert_id=alert.id,
        patient_id=payload.patient_id,
        severity=alert.severity,
        latitude=lat,
        longitude=lng,
        push=push["status"],
    )
