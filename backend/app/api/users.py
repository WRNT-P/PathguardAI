# pathguard/backend/app/api/users.py
"""User registration — creates the ``users`` row that GPS/risk/alert data
references by FK.

The Flutter app calls this once (e.g. after Firebase sign-in) so an internal
int ``users.id`` exists before any GPS for that patient is ingested. The app
keeps the returned id and sends it as ``patient_id`` on every later request.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

# Plain geometry, borrowed rather than copied: three haversine implementations
# already exist in app/ai and a fourth would be the one that drifts.
from app.ai.module2_prediction.cluster_matcher import haversine_km
from app.db import crud, rule_repository
from app.db.database import get_db
from app.services.auth import (
    Caller, current_caller, verified_uid, verify_patient_access,
)
from app.models.user_profile import UserCreate, UserResponse

router = APIRouter()

# Used only if the KB row is missing. Same number as the seed, stated twice on
# purpose: the ranking must not fail on a database nobody has re-seeded, and a
# silent 0 here would mark every caregiver unusable.
_FALLBACK_LOCATION_MAX_AGE_S = 1800.0


@router.post(
    "/api/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="ลงทะเบียนผู้ใช้ใหม่ (สร้าง users row จาก firebase_uid)",
)
async def register_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    uid: str | None = Depends(verified_uid),
) -> UserResponse:
    # Registering under someone else's uid would hand you their patient's
    # data for the life of the account, so the claim has to match the token.
    if uid is not None and uid != payload.firebase_uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="firebase_uid does not match the signed-in account",
        )
    if await crud.get_user_id_by_firebase_uid(db, payload.firebase_uid) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="user already registered for this firebase_uid",
        )

    user = await crud.create_user(
        db,
        firebase_uid=payload.firebase_uid,
        name=payload.name,
        role=payload.role,
        caregiver_id=payload.caregiver_id,
        phone=payload.phone,
    )
    # Built by hand rather than straight off the ORM row: ``caregiver_id`` stopped
    # being a column on ``users`` on 2026-08-28 and is now a patient_caregivers
    # link, so ``from_attributes`` has nothing to read. The response field stays —
    # the app is already parsing it.
    return UserResponse(
        id=user.id,
        firebase_uid=user.firebase_uid,
        name=user.name,
        role=user.role,
        caregiver_id=payload.caregiver_id,
        phone=user.phone,
        created_at=user.created_at,
    )


class CaregiverLocationIn(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class CaregiverLocationOut(BaseModel):
    caregiver_id: int
    location_updated_at: datetime


@router.put(
    "/api/caregivers/{caregiver_id}/location",
    response_model=CaregiverLocationOut,
    summary="แอปผู้ดูแลรายงานตำแหน่งตัวเอง (ทับของเดิม ไม่เก็บประวัติ)",
)
async def update_caregiver_location(
    caregiver_id: int,
    payload: CaregiverLocationIn,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> CaregiverLocationOut:
    """Where this caregiver is now, so an alert can rank them by distance.

    Lives in this router rather than a new ``caregivers.py`` on purpose: a new
    router has to be mounted in ``app/main.py``, ``tests/conftest.py`` and
    ``scripts/demo_server.py``, and those three lists drifting apart is what
    404'd a caregiver's pin at the dashboard's port in August.

    Only the latest position is kept and nothing reads a trail — see the columns
    in ``models.py``. **The caregiver app must ask for consent before it starts
    sending this.** It is a family member's location, not the patient's, and the
    one thing it is for is answering "who is closest" when the patient is
    missing.
    """
    # Writing someone else's location would put them at the top of a distance
    # ranking for a patient they are nowhere near — the same class of check as
    # registering a device token for another account, and the reason neither can
    # be a patient-access test.
    if caller.authenticated and caller.user_id != caregiver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="a location may only be reported for your own account",
        )

    user = await crud.get_user(db, caregiver_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user {caregiver_id} not found — call /api/register first",
        )
    # A patient's position belongs in gps_data through POST /api/gps, which
    # smooths it, scores it and can raise an alert. Accepting it here would put
    # it somewhere none of that happens, and it would look like it worked.
    if user.role != "caregiver":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"user {caregiver_id} is a {user.role}, not a caregiver — "
                   "a patient's position goes to POST /api/gps",
        )

    updated = await crud.update_user_location(
        db, caregiver_id, payload.latitude, payload.longitude)
    return CaregiverLocationOut(
        caregiver_id=caregiver_id,
        location_updated_at=updated.location_updated_at,
    )


# ── Who is nearest (report: "alert every caregiver, ranked by distance") ──────

class RankedCaregiver(BaseModel):
    caregiver_id: int
    name: str
    is_primary: bool
    phone: str | None = Field(
        None, description="เบอร์โทรของผู้ดูแล · null = ยังไม่เคยกรอก ให้ซ่อนปุ่มโทร")
    distance_m: float | None = Field(
        None, description="ระยะทางถึงตำแหน่งล่าสุดของผู้ป่วย · null = ไม่รู้ตำแหน่งผู้ดูแล")
    location_age_s: float | None = Field(
        None, description="ตำแหน่งของผู้ดูแลเก่ากี่วินาที · null = ไม่เคยส่งเลย")
    usable: bool = Field(
        ..., description="ตำแหน่งใหม่พอที่จะเชื่อได้ไหม · false = ยังอยู่ในรายการแต่ท้ายแถว")


class RankedCaregiversOut(BaseModel):
    patient_id: int
    patient_latitude: float | None
    patient_longitude: float | None
    max_age_s: float
    caregivers: list[RankedCaregiver]


@router.get(
    "/api/patients/{patient_id}/caregivers",
    response_model=RankedCaregiversOut,
    summary="ผู้ดูแลของผู้ป่วยคนนี้ เรียงตามระยะทางจากผู้ป่วย ใกล้สุดขึ้นก่อน",
)
async def rank_caregivers(
    patient_id: int,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> RankedCaregiversOut:
    """Order this patient's caregivers by how far they are from the patient.

    **Nobody is ever dropped from this list.** A caregiver whose position is too
    old, or who has never reported one at all, sinks to the bottom instead of
    disappearing. The alternative was tempting and wrong: filtering by freshness
    means that on the day the app's reporting interval turns out to be longer
    than the cut-off, this endpoint answers "nobody" — at the moment a patient
    is missing, to a family that is standing right there. A wrongly ordered list
    is recoverable by a human reading it; an empty one is not.

    That is also why the cut-off being unanswered by the app side does not block
    this: it decides *ordering*, not *membership*.

    Read-only. It does not push, does not write, and may be polled.
    """
    latest = await crud.get_latest_gps(db, patient_id)
    # Smoothed position when the Kalman filter has one, raw otherwise: the same
    # choice the map makes, so the ranking and the pin agree.
    if latest is None:
        p_lat = p_lng = None
    else:
        p_lat = latest.smooth_latitude if latest.smooth_latitude is not None else latest.latitude
        p_lng = latest.smooth_longitude if latest.smooth_longitude is not None else latest.longitude

    max_age_s = await rule_repository.get_threshold(
        db, rule_repository.CAREGIVER_LOCATION_MAX_AGE_S,
        default=_FALLBACK_LOCATION_MAX_AGE_S)

    now = datetime.now(timezone.utc)
    rows: list[RankedCaregiver] = []
    for user, is_primary in await crud.get_caregivers_with_location(db, patient_id):
        age_s = crud.location_age_seconds(user, now)

        distance_m = None
        if (p_lat is not None and user.last_latitude is not None
                and user.last_longitude is not None):
            distance_m = haversine_km(
                p_lat, p_lng, user.last_latitude, user.last_longitude) * 1000.0

        rows.append(RankedCaregiver(
            caregiver_id=user.id,
            name=user.name,
            is_primary=is_primary,
            phone=user.phone,
            distance_m=distance_m,
            location_age_s=age_s,
            usable=(distance_m is not None and age_s is not None
                    and age_s <= max_age_s),
        ))

    # Three tiers, then distance, then the primary wins a tie. `is_primary` is
    # not a permission and never has been — this is the one thing it decides.
    rows.sort(key=lambda c: (
        not c.usable,
        c.distance_m if c.distance_m is not None else float("inf"),
        not c.is_primary,
        c.caregiver_id,
    ))

    return RankedCaregiversOut(
        patient_id=patient_id,
        patient_latitude=p_lat,
        patient_longitude=p_lng,
        max_age_s=max_age_s,
        caregivers=rows,
    )
