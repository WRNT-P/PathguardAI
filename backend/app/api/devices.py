# pathguard/backend/app/api/devices.py
"""FCM device registration — where the caregiver's phone says "push me here".

Without a row in ``device_tokens`` an alert is written and goes nowhere, so the
caregiver app must call this after sign-in and again on every launch (Firebase
rotates the token on reinstall and can expire it at any time).
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.database import get_db
from app.services.auth import Caller, current_caller

router = APIRouter()


class DeviceTokenIn(BaseModel):
    user_id: int = Field(..., description="users.id of the CAREGIVER (from /api/register)")
    token: str = Field(..., min_length=10, max_length=255)
    platform: Literal["android", "ios", "web"] = "android"


class DeviceTokenOut(BaseModel):
    id: int
    user_id: int
    platform: str


@router.post(
    "/api/devices/token",
    response_model=DeviceTokenOut,
    status_code=status.HTTP_200_OK,
    summary="ลงทะเบียน FCM token ของเครื่องผู้ดูแล (เรียกซ้ำได้ ทับของเดิม)",
)
async def register_device_token(
    payload: DeviceTokenIn,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> DeviceTokenOut:
    # Registering someone else's user_id would redirect their alerts to your
    # phone, so this is the one check that cannot be a patient-access test.
    if caller.authenticated and caller.user_id != payload.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="a device token may only be registered for your own account",
        )
    if not await crud.user_exists(db, payload.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user {payload.user_id} not found — call /api/register first",
        )

    row = await crud.upsert_device_token(
        db, user_id=payload.user_id, token=payload.token, platform=payload.platform
    )
    return DeviceTokenOut(id=row.id, user_id=row.user_id, platform=row.platform)
