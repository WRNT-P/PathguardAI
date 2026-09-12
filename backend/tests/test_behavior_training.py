"""Module 1 learns from completed trips, with nobody calling a script.

Two things are pinned here. First the wiring: ingestion trains a profile at
all — before this, clustering, routine patterns and walking speed were written,
tested in isolation and never run against a live patient, so every profile in
production held only what a caregiver had typed.

Second, and the reason the learning is shaped the way it is: what gets learned
is a place the patient *chose in the app, walked to, arrived at, stayed at, and
came back to on another day*. Standing somewhere twice is not enough, because
that is also what getting lost in the same place twice looks like.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.module1_behavior.behavior_pipeline import analyze_behavior
from app.ai.module1_behavior.trip_learning import learn_places_from_trips
from app.db import crud
from tests.conftest import record_arrival

pytestmark = pytest.mark.asyncio

_LAT, _LNG = 13.7563, 100.5018
_PLACE = "ตลาดศาลายา"


async def _register_patient(client, uid: str) -> int:
    resp = await client.post(
        "/api/register", json={"firebase_uid": uid, "name": "P", "role": "patient"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _stay(patient_id: int, start: datetime, minutes: int, *, speed: float = 0.9,
          lat: float = _LAT, lng: float = _LNG) -> list[dict]:
    """Fixes at one spot, one per minute, jittered by GPS noise."""
    return [{
        "patient_id": patient_id,
        "latitude": lat + (i % 3) * 1e-6,
        "longitude": lng + (i % 2) * 1e-6,
        "speed": speed,
        "recorded_at": (start + timedelta(minutes=i)).isoformat(),
    } for i in range(minutes)]


async def _visit(client, db, patient_id: int, start: datetime, minutes: int,
                 **kwargs) -> None:
    """One completed trip: the arrival the app reports, and the stay that follows."""
    points = _stay(patient_id, start, minutes, **kwargs)
    assert (await client.post("/api/gps/batch", json={"points": points})).status_code == 200
    await record_arrival(db, patient_id, kwargs.get("lat", _LAT), kwargs.get("lng", _LNG),
                         _PLACE, start)
    await db.commit()


async def _learn(db, patient_id: int) -> None:
    """Run the learning pass the way ingestion does.

    Called directly because ingestion throttles to one pass per patient per 15
    minutes (gps.py::PROFILE_TRAIN_INTERVAL_S) and these tests seed several
    days of history in one second. That the throttle and the trigger work is
    pinned by their own tests below.
    """
    await analyze_behavior(db, patient_id)
    await db.commit()


async def test_two_completed_trips_on_different_days_teach_a_place(client, db_session):
    now = datetime.now(timezone.utc)
    patient_id = await _register_patient(client, "learn-trips")

    await _visit(client, db_session, patient_id, now - timedelta(days=1, minutes=25), 25)
    await _visit(client, db_session, patient_id, now - timedelta(minutes=30), 25,
                 speed=1.3)

    await _learn(db_session, patient_id)
    profile = await crud.get_behavioral_profile(db_session, patient_id)
    places = json.loads(profile.known_places)
    assert len(places) == 1, places
    learned = places[0]

    # Named by the patient's own choice of destination — this path never
    # produces the nameless clusters the raw-track one did.
    assert learned["place_name"] == _PLACE
    assert learned["source"] == "learned_trip"
    # Capped below the caregiver's top rank: only a human gets to say "she
    # lives here".
    assert learned["visit_frequency"] <= 40

    routine = json.loads(profile.routine_patterns)
    assert routine, "routine_patterns stayed empty"
    assert {"hour", "cluster_id", "probability", "samples"} <= set(routine[0])

    # Their own pace, averaged over the moving fixes (0.9 and 1.3).
    assert profile.avg_walking_speed_ms == pytest.approx(1.1, abs=0.05)
    assert profile.last_trained_at is not None


async def test_a_place_they_only_passed_through_is_not_learned(client, db_session):
    """Arriving and leaving within a few minutes says nothing about the place."""
    now = datetime.now(timezone.utc)
    patient_id = await _register_patient(client, "learn-passing")

    await _visit(client, db_session, patient_id, now - timedelta(days=1, minutes=4), 4)
    await _visit(client, db_session, patient_id, now - timedelta(minutes=6), 4)

    await _learn(db_session, patient_id)
    profile = await crud.get_behavioral_profile(db_session, patient_id)
    assert json.loads(profile.known_places) == []


def test_two_visits_on_the_same_day_are_one_errand():
    """Out and back in one afternoon is an event. A routine spans days.

    Asserted against the rule rather than the module default, because
    MIN_DISTINCT_DAYS is currently dialled down to 1 for the demo — see the
    ⚠️ comments in trip_learning.py. The rule itself has to keep working.
    """
    day = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)  # 10:00 in Bangkok
    visits = [
        {"latitude": _LAT, "longitude": _LNG, "destination_name": _PLACE,
         "arrived_at": day, "dwell_s": 1800},
        {"latitude": _LAT, "longitude": _LNG, "destination_name": _PLACE,
         "arrived_at": day + timedelta(hours=5), "dwell_s": 1800},
    ]

    assert learn_places_from_trips(visits, min_distinct_days=2) == []
    # The same two visits a day apart do teach the place.
    visits[1]["arrived_at"] = day + timedelta(days=1)
    assert len(learn_places_from_trips(visits, min_distinct_days=2)) == 1


async def test_standing_somewhere_without_a_trip_teaches_nothing(client, db_session):
    """The lost-patient case, stated as a test.

    Twenty-five minutes on the spot, twice, on different days — everything the
    old clustering needed — but the patient never chose to go there. Learning
    it would have made the place count as familiar and silenced the alerts
    that fire when they end up there again.
    """
    now = datetime.now(timezone.utc)
    patient_id = await _register_patient(client, "learn-no-trip")

    for start in (now - timedelta(days=1, minutes=25), now - timedelta(minutes=30)):
        points = _stay(patient_id, start, 25)
        assert (await client.post("/api/gps/batch", json={"points": points})).status_code == 200
    await db_session.commit()

    await _learn(db_session, patient_id)
    profile = await crud.get_behavioral_profile(db_session, patient_id)
    assert json.loads(profile.known_places) == []


async def test_standing_still_teaches_no_walking_speed(client, db_session):
    """Speed 0 is not a walking pace, and averaging it in would shrink every
    search area Module 4 draws for this patient."""
    now = datetime.now(timezone.utc)
    patient_id = await _register_patient(client, "learn-stationary")

    await _visit(client, db_session, patient_id, now - timedelta(minutes=30), 25,
                 speed=0.0)

    profile = await crud.get_behavioral_profile(db_session, patient_id)
    assert profile is not None
    assert profile.avg_walking_speed_ms is None


async def test_training_is_throttled(client, db_session):
    """A second reading seconds later must not re-run a 30-day learning pass."""
    now = datetime.now(timezone.utc)
    patient_id = await _register_patient(client, "learn-throttle")

    await _visit(client, db_session, patient_id, now - timedelta(minutes=30), 25)
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
