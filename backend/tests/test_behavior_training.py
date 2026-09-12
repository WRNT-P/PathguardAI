"""Module 1 learns from ingestion, with nobody calling a script.

``analyze_behavior`` existed, was tested in isolation, and had no caller: every
profile in production held only what a caregiver had typed into the pin form.
These tests pin the wiring — that a patient who visits the same place twice ends
up with that place, an hourly routine and their own walking speed on file — and
the throttle that keeps a 30-day DBSCAN pass off every single reading.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import crud

pytestmark = pytest.mark.asyncio

# Two stays, because place_clustering needs a place to be RE-visited (min 2) —
# once is a trip, twice is somewhere they go.
_LAT, _LNG = 13.7563, 100.5018


async def _register_patient(client, uid: str) -> int:
    resp = await client.post(
        "/api/register", json={"firebase_uid": uid, "name": "P", "role": "patient"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _stay(
    patient_id: int,
    start: datetime,
    minutes: int,
    *,
    speed: float,
    lat: float = _LAT,
    lng: float = _LNG,
) -> list[dict]:
    """A run of fixes at one spot, one per minute, jittered by GPS noise.

    Stay-point detection walks consecutive fixes by distance, not by clock, so
    two stays at the same coordinates with nothing in between collapse into one
    visit however many days apart they are — going somewhere else is what makes
    coming back a second visit.
    """
    return [{
        "patient_id": patient_id,
        "latitude": lat + (i % 3) * 1e-6,
        "longitude": lng + (i % 2) * 1e-6,
        "speed": speed,
        "recorded_at": (start + timedelta(minutes=i)).isoformat(),
    } for i in range(minutes)]


async def test_ingestion_learns_place_routine_and_walking_speed(client, db_session):
    now = datetime.now(timezone.utc)
    patient_id = await _register_patient(client, "learn-two-visits")

    # Home, out to one other place, then home again — 25 minutes each, all
    # clearing the 15-minute stay threshold. Home is the only place visited
    # twice, and twice is what makes a place.
    points = (
        _stay(patient_id, now - timedelta(days=1, minutes=25), 25, speed=0.9)
        + _stay(patient_id, now - timedelta(hours=3), 25, speed=1.1,
                lat=_LAT + 0.015, lng=_LNG + 0.015)
        + _stay(patient_id, now - timedelta(minutes=30), 25, speed=1.3)
    )
    resp = await client.post("/api/gps/batch", json={"points": points})
    assert resp.status_code == 200

    profile = await crud.get_behavioral_profile(db_session, patient_id)
    assert profile is not None

    places = json.loads(profile.known_places)
    assert len(places) == 1, places
    assert places[0]["source"] == "learned"
    # Capped below the caregiver's top rank: only a human gets to say "they live
    # here". Everything else about the place is learned.
    assert places[0]["visit_frequency"] <= 40
    assert places[0]["latitude"] == pytest.approx(_LAT, abs=1e-4)

    # When they are there, off the same history — the column Module 5's
    # time_match factor reads, and which nothing used to fill.
    routine = json.loads(profile.routine_patterns)
    assert routine, "routine_patterns stayed empty"
    assert {"hour", "cluster_id", "probability", "samples"} <= set(routine[0])
    assert all(0 <= p["hour"] <= 23 for p in routine)

    # Their own pace, averaged over the moving fixes (0.9 and 1.3), not a
    # population constant.
    assert profile.avg_walking_speed_ms == pytest.approx(1.1, abs=0.05)
    assert profile.last_trained_at is not None


async def test_standing_still_teaches_no_walking_speed(client, db_session):
    """Speed 0 is not a walking pace, and averaging it in would shrink every
    search area Module 4 draws for this patient."""
    now = datetime.now(timezone.utc)
    patient_id = await _register_patient(client, "learn-stationary")

    points = _stay(patient_id, now - timedelta(minutes=30), 25, speed=0.0)
    resp = await client.post("/api/gps/batch", json={"points": points})
    assert resp.status_code == 200

    profile = await crud.get_behavioral_profile(db_session, patient_id)
    assert profile is not None
    assert profile.avg_walking_speed_ms is None


async def test_training_is_throttled(client, db_session):
    """A second reading seconds later must not re-run a 30-day DBSCAN pass."""
    now = datetime.now(timezone.utc)
    patient_id = await _register_patient(client, "learn-throttle")

    first = _stay(patient_id, now - timedelta(minutes=30), 25, speed=1.0)
    assert (await client.post("/api/gps/batch", json={"points": first})).status_code == 200

    profile = await crud.get_behavioral_profile(db_session, patient_id)
    trained_at = profile.last_trained_at

    resp = await client.post("/api/gps", json={
        "patient_id": patient_id,
        "latitude": _LAT,
        "longitude": _LNG,
        "speed": 1.0,
        "recorded_at": now.isoformat(),
    })
    assert resp.status_code == 200

    await db_session.refresh(profile)
    assert profile.last_trained_at == trained_at
