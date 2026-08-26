"""Two places the report makes the patient's stage change what the backend does.

Both were promised in the report and implemented nowhere until 2026-08-26:

* the search radius narrows for a moderate-stage patient and widens for an
  early-stage one (report lines 261 and 353);
* the home screen shows three places to a Level 1 patient and five to a Level 2
  patient (report features table).

The second looks backwards until you read why: a Level 2 patient's search box is
locked, so the grid is the only way they can reach anywhere at all.

The test that matters most here is
``test_two_contractions_do_not_compound``. The low-wandering contraction was
already documented as a mid-stage proxy, so stage and wandering are partly the
same signal; multiplying them would shrink the area a missing patient is
searched in by 36% on the strength of one fact counted twice.
"""
from __future__ import annotations

import json

import pytest

from app.ai.module4_search_area.search_radius_adjustment import adjust_radius
from app.db import crud

# Deliberately no module-level asyncio mark: the radius tests are pure functions
# and marking them would only produce warnings. The DB-backed ones say so.
HOME = (13.7563, 100.5018)
BASE_M = 1000.0

# Far enough away that no place sits inside the base radius, so the
# "no familiar places" expansion is the only one in play unless a test wants it.
FAR_PLACE = [{"cluster_id": 0, "place_name": "บ้าน", "latitude": 14.5,
              "longitude": 100.5018, "visit_frequency": 100, "radius_m": 150}]
NEAR_PLACE = [{"cluster_id": 0, "place_name": "บ้าน", "latitude": HOME[0],
               "longitude": HOME[1], "visit_frequency": 100, "radius_m": 150}]


def radius(severity_level=None, wandering=None, places=NEAR_PLACE):
    return adjust_radius(BASE_M, places, *HOME, wandering_score=wandering,
                         severity_level=severity_level)


# ── search radius by stage ───────────────────────────────────────────────────

def test_moderate_stage_narrows_the_search():
    """They walk slowly and circle somewhere familiar (report line 353)."""
    assert radius(severity_level=2)["adjusted_radius_m"] < BASE_M


def test_early_stage_widens_the_search():
    """They still travel independently, so look further out."""
    assert radius(severity_level=1)["adjusted_radius_m"] > BASE_M


def test_an_unstated_stage_changes_nothing():
    """Guessing a stage would be a clinical claim nobody made."""
    assert radius(severity_level=None)["adjusted_radius_m"] == BASE_M


def test_a_stage_the_system_does_not_know_changes_nothing():
    assert radius(severity_level=7)["adjusted_radius_m"] == BASE_M


def test_the_stage_is_reported_in_the_reason_and_meta():
    r = radius(severity_level=2)
    assert "moderate stage" in r["adjustment_reason"]
    assert r["_meta"]["severity_level"] == 2


def test_two_contractions_do_not_compound():
    """Stage and low wandering are partly the same signal; 0.64 would be wrong.

    Too large a search area costs volunteers time. Too small a one costs a
    missing patient the ground they are actually standing on, so when several
    signals all say "narrower" the gentlest of them wins.
    """
    stage_only = radius(severity_level=2)["adjusted_radius_m"]
    wander_only = radius(wandering=0.1)["adjusted_radius_m"]
    both = radius(severity_level=2, wandering=0.1)["adjusted_radius_m"]

    assert both == stage_only == wander_only
    assert both > BASE_M * 0.64          # what multiplying them would have given


def test_expansions_still_compound():
    """An over-large area is the safe direction, so these multiply as before."""
    both = radius(severity_level=1, wandering=0.9, places=FAR_PLACE)
    assert both["adjusted_radius_m"] > BASE_M * 1.5


def test_wandering_alone_behaves_exactly_as_it_did():
    """Nothing about the pre-existing signals may have moved."""
    assert radius(wandering=0.9)["adjusted_radius_m"] == pytest.approx(BASE_M * 1.30)
    assert radius(wandering=0.1)["adjusted_radius_m"] == pytest.approx(BASE_M * 0.80)
    assert radius(places=FAR_PLACE)["adjusted_radius_m"] == pytest.approx(BASE_M * 1.50)


# ── how many places the home screen shows ────────────────────────────────────

FIVE_PLACES = [
    {"cluster_id": i, "place_name": f"place{i}",
     "latitude": HOME[0] + i * 0.01, "longitude": HOME[1],
     "visit_frequency": 100 - i * 10, "avg_stay_time": 3600.0 * (5 - i),
     "radius_m": 150, "source": "manual"}
    for i in range(6)
]


async def _patient(db_session, severity_level):
    caregiver = await crud.create_user(
        db_session, firebase_uid=f"cg-{severity_level}", name="cg", role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid=f"pt-{severity_level}", name="pt", role="patient",
        caregiver_id=caregiver.id, severity_level=severity_level)
    await db_session.flush()
    await crud.upsert_behavioral_profile(
        db_session, patient.id,
        known_places=json.dumps(FIVE_PLACES, ensure_ascii=False))
    await db_session.commit()
    return patient.id


@pytest.mark.asyncio
@pytest.mark.parametrize("severity_level,expected", [(1, 3), (2, 5), (None, 3)])
async def test_home_screen_length_follows_the_stage(
        client, db_session, severity_level, expected):
    patient_id = await _patient(db_session, severity_level)
    r = await client.get(f"/api/recommendation/{patient_id}")
    assert r.status_code == 200, r.text
    assert len(r.json()["recommendations"]) == expected


@pytest.mark.asyncio
async def test_the_extra_places_are_the_next_ranked_ones_not_arbitrary(
        client, db_session):
    """Level 2's list must start with the same places Level 1 would have seen."""
    one = await client.get(f"/api/recommendation/{await _patient(db_session, 1)}")
    two = await client.get(f"/api/recommendation/{await _patient(db_session, 2)}")

    ids_one = [p["cluster_id"] for p in one.json()["recommendations"]]
    ids_two = [p["cluster_id"] for p in two.json()["recommendations"]]
    assert ids_two[:3] == ids_one
