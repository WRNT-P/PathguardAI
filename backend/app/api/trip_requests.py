# pathguard/backend/app/api/trip_requests.py
"""Trip Approval — report C-3, the caregiver's answer to "may I go there?".

A Level 1 patient still reads a screen and decides for themselves; the report
gives them a working search box. A Level 2 patient's search box is locked, and
anywhere new has to go past their caregiver first. This is that gate.

The caregiver is shown a confidence score, and getting that score to mean
anything is the reason ``module5_recommend/trip_confidence.py`` exists —
Module 5's own ranker cannot exceed 0.350 on a place the patient has never been,
which is every request this endpoint will ever see. The score is computed once,
when the patient asks, and stored on the row: the caregiver is deciding about
that moment, and a number that drifted while the phone sat in a pocket would
answer a different question.

**On "reject → send an SOS to every caregiver" (report line 535).** The intent
is right — a Level 2 patient who has just been told no may set off anyway, and
the family should know to watch. Two things about the wording do not survive
contact with the schema:

* It is not an SOS. ``alert_type`` is ``"trip_denied"``, not ``"sos"``. The push
  cooldown is keyed on (patient_id, alert_type), so reusing ``sos`` would let a
  denied trip silently swallow a real button press for the next ten minutes —
  the exact collision ``api/sos.py`` gave itself a separate type to avoid. It
  also matters to the person reading the notification, who acts differently on
  "they asked to go somewhere" than on "they pressed the button".
* "Every caregiver" is still one caregiver in practice. The schema stopped
  being the reason on 2026-08-28 — ``patient_caregivers`` holds many — but no
  endpoint adds a second one yet, so the only recipient is the person who just
  pressed reject, and pushing to them would notify someone of their own
  decision. The alert row is written regardless; the push is skipped.

  **An earlier version of this comment said the rule "starts notifying the
  others without further change" once multi-caregiver landed. That was wrong.**
  The check below is ``the caregiver == the decider``, which with several
  caregivers skips the push to *all* of them, not just the decider. Making it
  right needs ``notify_alert`` to take an excluded recipient, which is why it is
  not free and why it is scheduled with the ranked fan-out rather than here.
"""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.module1_behavior.known_places import decode
from app.ai.module5_recommend.trip_confidence import score_destination
from app.db import crud, rule_repository
from app.db.database import get_db
from app.services.auth import (
    Caller, assert_may_access_patient, current_caller, verify_patient_access,
)
from app.services.notification import notify_alert

logger = logging.getLogger(__name__)

router = APIRouter()

# Only a Level 2 patient needs permission. The report is explicit that a Level 1
# patient "สามารถเดินทางเองได้" and asking them to wait for approval would take
# away independence they still have.
_APPROVAL_REQUIRED_LEVEL = 2

_DENIED_ALERT_TYPE = "trip_denied"
_FALLBACK_COOLDOWN_S = 600.0


class TripRequestIn(BaseModel):
    patient_id: int
    destination_name: str = Field(..., min_length=1, max_length=255)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class TripRequestOut(BaseModel):
    id: int | None = Field(
        None, description="null เมื่อ status = not_required (ไม่ได้บันทึกแถว)")
    patient_id: int
    destination_name: str
    latitude: float
    longitude: float
    status: str = Field(
        ..., description="pending | approved | rejected | not_required")
    confidence: float
    factors: dict = {}
    nearest_place_name: str | None = None
    nearest_place_distance_m: float | None = None
    blocking_zone_name: str | None = None
    confidence_status: str = Field(
        "ok", description='"no_profile" = ยังไม่มีหมุด จึงเทียบกับอะไรไม่ได้')
    created_at: datetime | None = None
    decided_at: datetime | None = None


class TripRequestsOut(BaseModel):
    patient_id: int
    requests: list[TripRequestOut]
    count: int


class DecisionIn(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject)$")


class DecisionOut(BaseModel):
    id: int
    patient_id: int
    status: str
    decided_at: datetime | None
    alert_id: int | None = Field(
        None, description="alert ที่เกิดจากการปฏิเสธ (null เมื่ออนุมัติ)")
    push: str | None = Field(
        None, description="sent | cooldown | no_caregiver | skipped_self | null")


def _row_to_out(row) -> TripRequestOut:
    factors = {}
    if row.factors:
        try:
            factors = json.loads(row.factors)
        except (json.JSONDecodeError, TypeError):        # pragma: no cover
            factors = {}
    return TripRequestOut(
        id=row.id,
        patient_id=row.patient_id,
        destination_name=row.destination_name,
        latitude=row.latitude,
        longitude=row.longitude,
        status=row.status,
        confidence=row.confidence,
        factors=factors,
        nearest_place_name=factors.get("nearest_place_name"),
        nearest_place_distance_m=factors.get("nearest_place_distance_m"),
        blocking_zone_name=factors.get("blocking_zone_name"),
        confidence_status=factors.get("confidence_status", "ok"),
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


@router.post(
    "/api/trip-requests",
    response_model=TripRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="ผู้ป่วยระยะกลางขออนุมัติเดินทางไปสถานที่ใหม่",
)
async def request_trip(
    payload: TripRequestIn,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> TripRequestOut:
    patient = await crud.get_user(db, payload.patient_id)
    if patient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"unknown patient_id {payload.patient_id}")
    await assert_may_access_patient(db, caller, payload.patient_id)

    if patient.severity_level != _APPROVAL_REQUIRED_LEVEL:
        # Not an error. The app should not have to encode the rule, and a 4xx
        # here would turn "you may simply go" into something that looks broken.
        return TripRequestOut(
            patient_id=payload.patient_id,
            destination_name=payload.destination_name,
            latitude=payload.latitude,
            longitude=payload.longitude,
            status="not_required",
            confidence=1.0,
        )

    profile = await crud.get_behavioral_profile(db, payload.patient_id)
    known_places = decode(profile.known_places if profile else None)
    danger_zones = await rule_repository.get_active_danger_zones(db)
    # Same ceiling the risk score deviates against, so the two scales agree and
    # an admin editing the KB moves both. Never hardcode it here (gotcha 3).
    ceiling_m = await rule_repository.get_threshold(
        db, rule_repository.ROUTE_DEVIATION_CEILING_M)

    scored = score_destination(
        payload.latitude, payload.longitude, known_places, danger_zones,
        decay_ceiling_m=ceiling_m,
    )
    factors = {
        **scored.factors,
        "nearest_place_name": scored.nearest_place_name,
        "nearest_place_distance_m": scored.nearest_place_distance_m,
        "blocking_zone_name": scored.blocking_zone_name,
        "confidence_status": scored.status,
        "scorer": scored.scorer,
    }

    row = await crud.create_trip_request(
        db,
        patient_id=payload.patient_id,
        destination_name=payload.destination_name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        confidence=scored.confidence,
        factors=json.dumps(factors, ensure_ascii=False),
    )
    logger.info("trip request %s: patient %s -> %s (confidence %.3f)",
                row.id, payload.patient_id, payload.destination_name,
                scored.confidence)
    return _row_to_out(row)


@router.get(
    "/api/patients/{patient_id}/trip-requests",
    response_model=TripRequestsOut,
    summary="ผู้ดูแลดูคำขอเดินทางของผู้ป่วย",
)
async def list_trip_requests(
    patient_id: int,
    status_filter: str | None = Query(
        None, alias="status", pattern="^(pending|approved|rejected)$"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> TripRequestsOut:
    if not await crud.user_exists(db, patient_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"unknown patient_id {patient_id}")
    rows = await crud.get_trip_requests(db, patient_id, status_filter, limit)
    return TripRequestsOut(
        patient_id=patient_id,
        requests=[_row_to_out(r) for r in rows],
        count=len(rows),
    )


@router.patch(
    "/api/trip-requests/{request_id}",
    response_model=DecisionOut,
    summary="ผู้ดูแลอนุมัติหรือปฏิเสธคำขอเดินทาง",
)
async def decide_trip(
    request_id: int,
    payload: DecisionIn,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> DecisionOut:
    row = await crud.get_trip_request(db, request_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"unknown trip request {request_id}")
    await assert_may_access_patient(db, caller, row.patient_id)

    if row.status != "pending":
        # Deciding twice would overwrite a decision the family may have already
        # acted on, and the second decider would never know.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"trip request {request_id} was already {row.status}",
        )

    decided = "approved" if payload.decision == "approve" else "rejected"
    await crud.decide_trip_request(db, row, decided, caller.user_id)

    if decided == "approved":
        return DecisionOut(id=row.id, patient_id=row.patient_id,
                           status=row.status, decided_at=row.decided_at)

    alert = await crud.save_alert(
        db,
        row.patient_id,
        alert_type=_DENIED_ALERT_TYPE,
        severity="high",
        message=(
            f"คำขอเดินทางไป \"{row.destination_name}\" ถูกปฏิเสธ — "
            "ผู้ป่วยอาจออกไปเองแม้ไม่ได้รับอนุมัติ"
        ),
        latitude=row.latitude,
        longitude=row.longitude,
    )

    # Report line 535 says notify every caregiver. Today that set is one person
    # and it is the person who just pressed reject, so pushing would notify them
    # of their own decision. The alert row is written either way — the timeline
    # and the dashboard both read it.
    #
    # ⚠️ This is deliberately still the single-caregiver rule. The moment a
    # second caregiver can be added, "the decider is the only recipient" stops
    # being true and this silently drops the push for everybody else. See the
    # module docstring — it needs an excluded-recipient argument on notify_alert,
    # not a wider query here.
    caregivers = await crud.get_caregiver_ids(db, row.patient_id)
    if caller.authenticated and caregivers == [caller.user_id]:
        push_status = "skipped_self"
    else:
        try:
            cooldown = await rule_repository.get_threshold(
                db, rule_repository.PUSH_COOLDOWN_SECONDS)
        except Exception:                                # pragma: no cover
            cooldown = _FALLBACK_COOLDOWN_S
        push_status = (await notify_alert(db, alert, cooldown))["status"]

    logger.info("trip request %s rejected by %s (push: %s)",
                row.id, caller.user_id, push_status)
    return DecisionOut(id=row.id, patient_id=row.patient_id, status=row.status,
                       decided_at=row.decided_at, alert_id=alert.id,
                       push=push_status)
