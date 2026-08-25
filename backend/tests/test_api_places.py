"""Phase 5 — the caregiver's pin endpoint.

The point of this router is not storage, it is that pinning places moves a patient
out of partial scoring into the full five factors on the day they are enrolled.
The last test here is the one that matters; the rest guard the traps that make
pins silently useless (non-contiguous ids, equal familiarity, erased pins).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.api.places import STAY_SECONDS, VISIT_FREQUENCY
from app.db import crud

pytestmark = pytest.mark.asyncio

HOME = {"place_name": "บ้าน", "latitude": 13.7563, "longitude": 100.5018,
        "visit_rank": "daily_live", "stay_rank": "all_day"}
MARKET = {"place_name": "ตลาด", "latitude": 13.7600, "longitude": 100.5060,
          "visit_rank": "most_days", "stay_rank": "brief", "radius_m": 400}


async def _register_patient(client, uid: str) -> int:
    resp = await client.post(
        "/api/register", json={"firebase_uid": uid, "name": "P", "role": "patient"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_pins_are_stored_in_the_shape_the_ai_modules_read(client, db_session):
    patient_id = await _register_patient(client, "pin-shape")

    resp = await client.post(
        f"/api/patients/{patient_id}/places", json={"places": [HOME, MARKET]}
    )
    assert resp.status_code == 201

    profile = await crud.get_behavioral_profile(db_session, patient_id)
    stored = json.loads(profile.known_places)

    # Contiguous from 0 — route_prediction.py:111 sizes an n x n matrix from
    # max(cluster_id) + 1, so a gap here costs memory, not correctness.
    assert [p["cluster_id"] for p in stored] == [0, 1]

    # Ranks became the numbers the modules actually consume.
    assert stored[0]["visit_frequency"] == VISIT_FREQUENCY["daily_live"]
    assert stored[0]["avg_stay_time"] == STAY_SECONDS["all_day"]
    assert stored[1]["visit_frequency"] == VISIT_FREQUENCY["most_days"]

    # …and they differ, which is the whole reason the API takes ranks. Equal
    # frequencies divide out to familiarity 1.0 everywhere (cluster_matcher.py:34-38)
    # and the system stops flagging unfamiliar places at all.
    assert stored[0]["visit_frequency"] != stored[1]["visit_frequency"]

    assert stored[0]["radius_m"] == 150       # default: one house
    assert stored[1]["radius_m"] == 400       # a market needs more
    assert all(p["source"] == "manual" for p in stored)


async def test_get_round_trips_the_caregivers_answers(client):
    patient_id = await _register_patient(client, "pin-roundtrip")
    await client.post(f"/api/patients/{patient_id}/places", json={"places": [HOME]})

    resp = await client.get(f"/api/patients/{patient_id}/places")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    # The dropdown re-selects what they chose, rather than guessing back from 100.
    assert body["places"][0]["visit_rank"] == "daily_live"
    assert body["places"][0]["stay_rank"] == "all_day"
    assert body["places"][0]["place_name"] == "บ้าน"


async def test_posting_again_replaces_pins_but_keeps_what_module_1_learned(
    client, db_session
):
    """Gotcha #5 in the other direction: a caregiver saving must not wipe learning."""
    patient_id = await _register_patient(client, "pin-merge")
    learned = {"cluster_id": 7, "place_name": "clustered", "latitude": 13.80,
               "longitude": 100.60, "visit_frequency": 22, "avg_stay_time": 500.0,
               "source": "learned"}
    await crud.upsert_behavioral_profile(
        db_session, patient_id, known_places=json.dumps([learned]))
    await db_session.commit()

    resp = await client.post(
        f"/api/patients/{patient_id}/places", json={"places": [HOME, MARKET]})
    assert resp.status_code == 201

    places = resp.json()["places"]
    assert [p["source"] for p in places] == ["manual", "manual", "learned"]
    # Renumbered as one set — the learned place keeps its identity, not its old id.
    assert [p["cluster_id"] for p in places] == [0, 1, 2]
    assert places[2]["visit_rank"] is None   # never came from a dropdown


async def test_second_save_does_not_accumulate_duplicate_pins(client):
    patient_id = await _register_patient(client, "pin-replace")
    await client.post(f"/api/patients/{patient_id}/places", json={"places": [HOME]})
    resp = await client.post(
        f"/api/patients/{patient_id}/places", json={"places": [HOME, MARKET]})
    assert resp.json()["count"] == 2


async def test_unknown_patient_is_told_to_register(client):
    resp = await client.post("/api/patients/999999/places", json={"places": [HOME]})
    assert resp.status_code == 404
    assert "register" in resp.json()["detail"]


async def test_empty_and_invalid_payloads_are_rejected(client):
    patient_id = await _register_patient(client, "pin-invalid")
    assert (await client.post(
        f"/api/patients/{patient_id}/places", json={"places": []})).status_code == 422
    assert (await client.post(
        f"/api/patients/{patient_id}/places",
        json={"places": [{**HOME, "visit_rank": "sometimes"}]})).status_code == 422
    assert (await client.post(
        f"/api/patients/{patient_id}/places",
        json={"places": [{**HOME, "latitude": 999.0}]})).status_code == 422


async def test_pinning_switches_scoring_from_partial_to_full(client):
    """Why this endpoint exists.

    Same patient, same GPS, one difference: pins. Without them risk.py drops
    route_deviation, unfamiliarity and confusion — all three need known_places —
    and tags the result "partial"; with them the patient is scored on all five
    factors from their first day, with no history.
    """
    patient_id = await _register_patient(client, "pin-unlock")
    now = datetime.now(timezone.utc)
    points = [{
        "patient_id": patient_id,
        "latitude": 13.7563 + (i % 3) * 1e-6,
        "longitude": 100.5018 + (i % 2) * 1e-6,
        "speed": 0.0,
        "recorded_at": (now - timedelta(seconds=30 * (30 - i))).isoformat(),
    } for i in range(30)]
    assert (await client.post("/api/gps/batch", json={"points": points})).status_code == 200

    before = (await client.get(f"/api/risk/{patient_id}")).json()
    assert before["status"] == "partial"
    assert set(before["contributions"]) == {"wandering", "danger_zone"}

    await client.post(f"/api/patients/{patient_id}/places", json={"places": [HOME, MARKET]})

    after = (await client.get(f"/api/risk/{patient_id}")).json()
    assert after["status"] == "ok"
    assert set(after["contributions"]) == {
        "route_deviation", "wandering", "confusion", "danger_zone", "unfamiliarity"}
    # Sitting at a pinned home is now recognisable as being somewhere it belongs.
    assert after["contributions"]["unfamiliarity"] == 0.0
    assert after["risk_level"] == "low"
