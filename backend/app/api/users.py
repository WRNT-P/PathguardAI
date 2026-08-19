# pathguard/backend/app/api/users.py
"""User registration — creates the ``users`` row that GPS/risk/alert data
references by FK.

The Flutter app calls this once (e.g. after Firebase sign-in) so an internal
int ``users.id`` exists before any GPS for that patient is ingested. The app
keeps the returned id and sends it as ``patient_id`` on every later request.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.database import get_db
from app.models.user_profile import UserCreate, UserResponse

router = APIRouter()


@router.post(
    "/api/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="ลงทะเบียนผู้ใช้ใหม่ (สร้าง users row จาก firebase_uid)",
)
async def register_user(
    payload: UserCreate, db: AsyncSession = Depends(get_db)
) -> UserResponse:
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
    return user
