# pathguard/backend/app/api/places.py
"""Caregiver-pinned places — the write path for ``behavioral_profiles.known_places``.

Until this router existed that column had exactly one writer,
``app/mock/seed_module5.py``, so a real patient could never have a place on file.

Pins are what switch risk scoring out of partial mode into the full five factors,
and that turns out to be the whole unlock: measured, a patient with pins and *no*
history at all scores 16.5 at home and 63.5 two kilometres away — within a point of
the same patient carrying a week of recorded trips.

The API speaks in ranks ("comes here most days"), never in the numbers the AI reads.
``API_CONTRACT_ADMIN.md`` at the backend root is the contract the admin UI is built
against; keep the two in step.
"""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.module1_behavior.known_places import (
    VISIT_FREQUENCY, decode, renumber,
)
from app.db import crud
from app.db.database import get_db
from app.services.auth import Caller, verify_patient_access

router = APIRouter()

VisitRank = Literal["daily_live", "most_days", "weekly", "rare"]
StayRank = Literal["all_day", "few_hours", "about_hour", "brief"]

# Familiarity is a RELATIVE measure — cluster_matcher.py:34-38 divides each place's
# visit_frequency by the largest one — so a caregiver typing similar numbers into
# every pin would make every place equally familiar and the system would quietly
# stop flagging strange ones. Ranks keep the values apart by construction, which is
# why the API refuses raw numbers. The numbers themselves live in
# ai/module1_behavior/known_places.py, which is also what rescales Module 1's
# learned places onto this same axis before the two are ever mixed.

# Seconds. Module 5 uses this to tell a place the patient passes through from one
# they spend their life in (recommendation_generation.py:69-74).
STAY_SECONDS: dict[str, float] = {
    "all_day": 28800.0,   # 8 h+
    "few_hours": 7200.0,
    "about_hour": 3600.0,
    "brief": 900.0,
}

# A house. A hospital or market compound needs more, or every walk across the
# grounds reads as leaving a familiar place — see find_nearest_cluster.
DEFAULT_RADIUS_M = 150

_MAX_PLACES = 50


class PlaceIn(BaseModel):
    place_name: str = Field(..., min_length=1, max_length=255)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    visit_rank: VisitRank
    stay_rank: StayRank
    radius_m: int = Field(default=DEFAULT_RADIUS_M, ge=20, le=5000)
    # Which pin is the patient's residence. Say it explicitly: it decides what
    # PUT .../places/home replaces, and guessing it from list order means a UI
    # that sorts or lets the caregiver drag rows silently moves the home — and
    # the next home update then deletes a real place.
    is_home: bool = False


class PlacesIn(BaseModel):
    """The caregiver's complete set of pins, not a single addition.

    Whole-set writes because ``cluster_id`` must stay contiguous from 0:
    route_prediction.py:111 does ``n = max(cluster_id) + 1`` and allocates an n x n
    matrix, so ids handed out one request at a time would drift and a stale id in
    the thousands would size a matrix in the millions.
    """
    places: list[PlaceIn] = Field(..., min_length=1, max_length=_MAX_PLACES)

    @model_validator(mode="after")
    def _at_most_one_home(self) -> "PlacesIn":
        marked = [p.place_name for p in self.places if p.is_home]
        if len(marked) > 1:
            raise ValueError(
                f"only one place may be is_home, got {len(marked)}: {marked}"
            )
        return self

    # min_length=1 is deliberate, not an oversight. A caregiver correcting a
    # wrong pin re-sends the corrected set; the only state it forbids is *zero*
    # pins, which drops scoring back to partial mode — measured, a patient
    # sitting at home and a patient lost 2.5 km away both read 18.8 low there,
    # so the system stops being able to tell them apart. It is also what lets
    # places[0] be the home unconditionally, below.


class HomePlaceIn(BaseModel):
    """The single pin the caregiver's "add a patient" screen collects.

    No ranks. The patient's residence is ``daily_live`` / ``all_day`` by
    definition, and a home pinned as ``rare`` would read the patient as a
    stranger in their own living room.
    """
    place_name: str = Field(..., min_length=1, max_length=255)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    radius_m: int = Field(default=DEFAULT_RADIUS_M, ge=20, le=5000)


class PlaceOut(BaseModel):
    cluster_id: int
    place_name: str
    latitude: float
    longitude: float
    # None on places Module 1 clustered for itself: they were never a dropdown
    # choice, so there is no rank to send back and the UI shows them read-only.
    visit_rank: VisitRank | None = None
    stay_rank: StayRank | None = None
    radius_m: int | None = None
    source: str
    # Exactly one pin per patient carries this — see set_home_place.
    is_home: bool = False


class PlacesOut(BaseModel):
    patient_id: int
    places: list[PlaceOut]
    count: int


# A learned routine is derived from the pin set, and it refers to places by
# ``cluster_id`` — which ``renumber`` hands out by position and reassigns on every
# write. So the moment the pins change, a stored routine can point at a different
# place than the one it learned, with nothing to signal it: "usually at the temple
# at 9" silently becomes "usually at home at 9". Both writers below therefore drop
# the routine, and scripts/build_routine_patterns.py rebuilds it. Losing the
# routine costs Module 5 one factor until that runs; keeping a stale one costs
# correctness with no way to notice.
_ROUTINE_INVALIDATED = "[]"


async def _require_patient(db: AsyncSession, patient_id: int) -> None:
    if not await crud.user_exists(db, patient_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown patient_id {patient_id} — call /api/register first",
        )


def _to_record(place: PlaceIn) -> dict:
    """One pin in the shape every AI module reads.

    Both the rank and the number it maps to are stored: the number is what the
    modules consume, the rank is what the caregiver actually chose, kept so the
    edit screen can re-select their answer instead of guessing backwards from 40.
    """
    return {
        "place_name": place.place_name,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "visit_frequency": VISIT_FREQUENCY[place.visit_rank],
        "avg_stay_time": STAY_SECONDS[place.stay_rank],
        "radius_m": place.radius_m,
        "visit_rank": place.visit_rank,
        "stay_rank": place.stay_rank,
        "source": "manual",
        "is_home": place.is_home,
    }


def _to_response(place: dict) -> PlaceOut:
    return PlaceOut(
        cluster_id=place["cluster_id"],
        place_name=place.get("place_name") or place.get("name") or "unnamed",
        latitude=place["latitude"],
        longitude=place["longitude"],
        visit_rank=place.get("visit_rank"),
        stay_rank=place.get("stay_rank"),
        radius_m=place.get("radius_m"),
        source=place.get("source", "learned"),
        is_home=bool(place.get("is_home")),
    )


@router.post(
    "/api/patients/{patient_id}/places",
    response_model=PlacesOut,
    status_code=status.HTTP_201_CREATED,
    summary="บันทึกหมุดสถานที่ทั้งชุดของผู้ป่วยหนึ่งคน",
)
async def set_places(
    patient_id: int,
    payload: PlacesIn,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> PlacesOut:
    await _require_patient(db, patient_id)

    profile = await crud.get_behavioral_profile(db, patient_id)
    # Replace the caregiver's pins, keep whatever Module 1 learned — the mirror
    # of what analyze_behavior does from the other side. Neither writer of this
    # column may delete the other's rows.
    stored = decode(profile.known_places if profile else None)
    learned = [p for p in stored if p.get("source") != "manual"]
    manual = [_to_record(p) for p in payload.places]
    # Exactly one pin must end up flagged, so PUT .../places/home below always
    # has a row to replace rather than a duplicate to append. The caller says
    # which; falling back to the first pin only when they said nothing keeps
    # older clients working, and min_length=1 guarantees there is a first pin.
    if not any(p["is_home"] for p in manual):
        manual[0]["is_home"] = True
    places = renumber(manual + learned)

    await crud.upsert_behavioral_profile(
        db, patient_id,
        known_places=json.dumps(places, ensure_ascii=False),
        routine_patterns=_ROUTINE_INVALIDATED,
    )
    return PlacesOut(
        patient_id=patient_id,
        places=[_to_response(p) for p in places],
        count=len(places),
    )


@router.put(
    "/api/patients/{patient_id}/places/home",
    response_model=PlacesOut,
    summary="ตั้ง/แก้หมุดบ้าน (สถานที่ปลอดภัย) จุดเดียว โดยไม่แตะหมุดอื่น",
)
async def set_home_place(
    patient_id: int,
    payload: HomePlaceIn,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> PlacesOut:
    """Upsert the home pin alone.

    Exists because ``POST .../places`` replaces the caregiver's whole manual set.
    The app's "add a patient" screen collects one safe place, so re-sending it
    after a full set had been pinned would delete the rest — silently, with a
    201, and scoring would degrade to 56-medium at every routine place the family
    visits. That hazard is removed here rather than by asking the app to
    remember: this route cannot delete a pin it did not write.
    """
    await _require_patient(db, patient_id)

    profile = await crud.get_behavioral_profile(db, patient_id)
    stored = decode(profile.known_places if profile else None)

    home = _to_record(
        PlaceIn(
            place_name=payload.place_name,
            latitude=payload.latitude,
            longitude=payload.longitude,
            visit_rank="daily_live",
            stay_rank="all_day",
            radius_m=payload.radius_m,
        )
    )
    home["is_home"] = True

    manual = [p for p in stored if p.get("source") == "manual"]
    learned = [p for p in stored if p.get("source") != "manual"]
    if any(p.get("is_home") for p in manual):
        others = [p for p in manual if not p.get("is_home")]
    else:
        # Pinned before the flag existed. The invariant is the same either way —
        # places[0] is the home — so fall back to position, or a profile written
        # last week would end up with the new home *and* the old one.
        others = manual[1:]
    places = renumber([home] + others + learned)

    await crud.upsert_behavioral_profile(
        db, patient_id,
        known_places=json.dumps(places, ensure_ascii=False),
        routine_patterns=_ROUTINE_INVALIDATED,
    )
    return PlacesOut(
        patient_id=patient_id,
        places=[_to_response(p) for p in places],
        count=len(places),
    )


@router.get(
    "/api/patients/{patient_id}/places",
    response_model=PlacesOut,
    summary="อ่านหมุดสถานที่ของผู้ป่วย (ไว้แสดงตอนแก้ไข)",
)
async def get_places(
    patient_id: int,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> PlacesOut:
    await _require_patient(db, patient_id)
    profile = await crud.get_behavioral_profile(db, patient_id)
    stored = decode(profile.known_places if profile else None)
    places = [p for p in stored if "cluster_id" in p]
    return PlacesOut(
        patient_id=patient_id,
        places=[_to_response(p) for p in places],
        count=len(places),
    )
