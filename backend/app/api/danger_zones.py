# pathguard/backend/app/api/danger_zones.py
"""Danger-zone write path — the geofences a caregiver marks as unsafe.

``danger_zones`` had no writer at all outside the demo seeder, which left the one
risk factor that needs no learning at all sitting at zero. ``danger_zone`` is 15% of
the score and it is exact from the first minute: no profile, no history, no fitted
model — just whether the patient is inside a circle somebody drew. For a trial that
starts before the system knows the patient, it is the most reliable signal there is.

Zones are part of the Module 3 rule knowledge base, so the writes go through
``rule_repository``, which versions them and writes a ``rule_audit_log`` row in the
same transaction (design Q4) rather than mutating rows behind the trail's back.

Unauthenticated, like every other router here (decision D5) — see the note in
``admin_rules.py``.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import rule_repository
from app.db.database import get_db
from app.services.auth import Caller, current_caller
from app.db.models import DangerZone
from app.db.rule_repository import RuleValidationError

router = APIRouter()

ZoneType = Literal["highway", "waterway", "construction", "other"]

# Zones are not tied to a patient and are shared by everyone, so the trail needs to
# say where a row came from. Rules seeded from the medical sources carry a citation;
# these carry the caregiver who drew them.
_SOURCE_REFERENCE = "caregiver_input"


class DangerZoneCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    center_latitude: float = Field(..., ge=-90, le=90)
    center_longitude: float = Field(..., ge=-180, le=180)
    radius_meters: float = Field(..., gt=0, le=10_000)
    zone_type: ZoneType
    # Required, and deliberately so: rule_audit_log and the KB design both hold that
    # every rule states why it exists, so a zone can be reviewed later by someone
    # who was not there when it was drawn.
    rationale: str = Field(..., min_length=1)
    created_by: str = Field(default="admin_ui", max_length=100)


class DangerZoneOut(BaseModel):
    id: int
    name: str
    center_latitude: float
    center_longitude: float
    radius_meters: float
    zone_type: str
    rationale: str
    active: bool

    model_config = {"from_attributes": True}


async def _get_zone(db: AsyncSession, zone_id: int) -> DangerZone:
    zone = (await db.execute(
        select(DangerZone).where(DangerZone.id == zone_id)
    )).scalar_one_or_none()
    if zone is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no danger zone with id={zone_id}",
        )
    return zone


@router.post(
    "/api/danger-zones",
    response_model=DangerZoneOut,
    status_code=status.HTTP_201_CREATED,
    summary="เพิ่มเขตอันตรายหนึ่งเขต",
)
async def create_danger_zone(
    payload: DangerZoneCreate,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(current_caller),
) -> DangerZoneOut:
    try:
        zone_id = await rule_repository.add_danger_zone(
            db,
            name=payload.name,
            latitude=payload.center_latitude,
            longitude=payload.center_longitude,
            radius_m=payload.radius_meters,
            zone_type=payload.zone_type,
            source_reference=_SOURCE_REFERENCE,
            rationale=payload.rationale,
            changed_by=payload.created_by,
            reason="added via admin UI",
        )
    except RuleValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return DangerZoneOut.model_validate(await _get_zone(db, zone_id))


@router.get(
    "/api/danger-zones",
    response_model=list[DangerZoneOut],
    summary="อ่านเขตอันตรายที่ยังใช้งานอยู่ทั้งหมด",
)
async def list_danger_zones(
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(current_caller),
) -> list[DangerZoneOut]:
    rows = (await db.execute(
        select(DangerZone).where(DangerZone.active).order_by(DangerZone.id)
    )).scalars().all()
    return [DangerZoneOut.model_validate(z) for z in rows]


@router.delete(
    "/api/danger-zones/{zone_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="ปิดการใช้งานเขตอันตราย (ไม่ลบจริง เก็บประวัติไว้)",
)
async def deactivate_danger_zone(
    zone_id: int,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(current_caller),
) -> Response:
    # Deactivated, never deleted: the audit trail has to keep pointing at a row
    # that still exists, and a zone that was live during an incident stays part of
    # the record of that incident.
    try:
        await rule_repository.deactivate_danger_zone(
            db, zone_id, changed_by="admin_ui", reason="removed via admin UI"
        )
    except RuleValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
