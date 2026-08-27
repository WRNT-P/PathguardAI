"""What ``GET /api/recommendation/{patient_id}`` actually hands the app.

Written 2026-08-27 while documenting the endpoint — it is one of four that had
never appeared in either contract file, and the patient app is mocking its
output today. Two things were wrong the moment anyone looked:

* **No ``place_name``.** Module 4 has carried place names since it was written
  (``TargetLocation.name``, ``FamiliarPath.place_name``) and so does
  ``trip_confidence``; Module 5 alone dropped them, so the endpoint answered a
  dementia patient's home screen with latitude and longitude. That is not a
  formatting problem — there is nothing the app could have rendered.
* **``time_match_available`` was hardcoded ``False``.** True when it was
  written and false since 2026-08-26, when ``routine_patterns`` gained a writer
  and ``time_match`` gained weight 0.25. A flag that reports a live factor as
  dead teaches the app to ignore it.

Both are covered here rather than in ``test_module5_recommendation.py`` because
what is being pinned is the *response the app parses*, not the scorer.
"""
from __future__ import annotations

import json

import pytest

from app.ai.module1_behavior.routine_patterns import LOCAL_UTC_OFFSET_HOURS
from app.db import crud

pytestmark = pytest.mark.asyncio

HOME = (13.7563, 100.5018)

PINNED = [
    {"cluster_id": 0, "place_name": "บ้าน", "latitude": HOME[0], "longitude": HOME[1],
     "visit_frequency": 100, "avg_stay_time": 28800.0, "radius_m": 150,
     "source": "manual", "is_home": True},
    {"cluster_id": 1, "place_name": "วัด", "latitude": 13.7601, "longitude": 100.5062,
     "visit_frequency": 40, "avg_stay_time": 3600.0, "radius_m": 400,
     "source": "manual"},
]

# What Module 1's clustering emits: no name, because place_clustering.py has no
# way to produce one. Kept in the fixture so the null case is a real shape and
# not a hand-made one.
LEARNED = [{"cluster_id": 2, "latitude": 13.77, "longitude": 100.51,
            "visit_frequency": 12, "avg_stay_time": 900.0, "source": "learned"}]


async def make_patient(db_session, places, routine=None):
    caregiver = await crud.create_user(
        db_session, firebase_uid="cg-rec", name="cg", role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid="pt-rec", name="pt", role="patient",
        caregiver_id=caregiver.id, severity_level=2)
    await db_session.flush()
    await crud.upsert_behavioral_profile(
        db_session, patient.id,
        known_places=json.dumps(places, ensure_ascii=False),
        routine_patterns=json.dumps(routine) if routine is not None else None)
    await db_session.commit()
    return patient.id


async def test_pinned_places_come_back_with_the_name_the_caregiver_gave_them(
        client, db_session):
    """The blocking one: the patient's home screen has to say "บ้าน", not 13.7563."""
    patient_id = await make_patient(db_session, PINNED)

    r = await client.get(f"/api/recommendation/{patient_id}")

    assert r.status_code == 200, r.text
    names = {p["place_name"] for p in r.json()["recommendations"]}
    assert names == {"บ้าน", "วัด"}


async def test_a_learned_place_has_no_name_and_says_so(client, db_session):
    """``null``, never a placeholder — "unknown" on a tile is worse than a blank
    one, because the app cannot tell it apart from a place actually called that."""
    patient_id = await make_patient(db_session, PINNED + LEARNED)

    r = await client.get(f"/api/recommendation/{patient_id}")

    by_id = {p["cluster_id"]: p for p in r.json()["recommendations"]}
    assert by_id[2]["place_name"] is None


async def test_time_match_is_reported_dead_when_the_patient_has_no_routine(
        client, db_session):
    patient_id = await make_patient(db_session, PINNED)

    r = await client.get(f"/api/recommendation/{patient_id}")

    assert r.json()["flags"]["time_match_available"] is False


async def test_time_match_is_reported_live_once_a_routine_exists(
        client, db_session):
    """Was hardcoded False, so this stayed false after L3-4 shipped the writer."""
    routine = [{"hour": h, "cluster_id": 0, "probability": 0.8} for h in range(24)]
    patient_id = await make_patient(db_session, PINNED, routine=routine)

    r = await client.get(f"/api/recommendation/{patient_id}")

    assert r.json()["flags"]["time_match_available"] is True
    assert LOCAL_UTC_OFFSET_HOURS  # the lookup is in local hours, not UTC


async def test_a_patient_with_no_profile_gets_an_empty_list_not_a_404(
        client, db_session):
    """The app polls this on the home screen; a 404 before the first pin would
    read as "the patient does not exist"."""
    patient_id = await make_patient(db_session, [])

    r = await client.get(f"/api/recommendation/{patient_id}")

    assert r.status_code == 200
    assert r.json()["status"] == "no_profile"
    assert r.json()["recommendations"] == []
