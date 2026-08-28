# pathguard/backend/app/api/users.py
"""User registration — creates the ``users`` row that GPS/risk/alert data
references by FK.

The Flutter app calls this once (e.g. after Firebase sign-in) so an internal
int ``users.id`` exists before any GPS for that patient is ingested. The app
keeps the returned id and sends it as ``patient_id`` on every later request.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.database import get_db
from app.services.auth import Caller, current_caller, verified_uid
from app.models.user_profile import UserCreate, UserResponse

router = APIRouter()


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
