"""Trip Approval (report C-3) — the caregiver gate in front of a new destination.

Three things these tests hold down, all of which the report's own wording would
break if implemented literally:

* the confidence a caregiver sees has to be able to exceed 0.350, which is why
  it comes from ``trip_confidence`` and not from ``score_place``;
* a rejected trip raises ``trip_denied``, **not** ``sos`` — the push cooldown is
  keyed on (patient_id, alert_type), so sharing the key would let a denied trip
  swallow a real button press for ten minutes;
* "notify every caregiver" is one caregiver today, and that caregiver is the
  person who just pressed reject, so the push is skipped rather than telling
  someone about their own decision. The alert row is still written.

A Level 1 patient never reaches any of this: the report gives them a working
search box and taking that away would remove independence they still have.
"""
from __future__ import annotations

import json
import math

import pytest
from sqlalchemy import select

from app.db import crud
from app.db.models import Alert, TripRequest
from app.services import auth

pytestmark = pytest.mark.asyncio

HOME = (13.7563, 100.5018)
METRES_PER_DEG_LAT = 2 * math.pi * 6_371_000.0 / 360.0


def north(metres: float) -> tuple[float, float]:
    return (HOME[0] + metres / METRES_PER_DEG_LAT, HOME[1])


PINS = [
    {"cluster_id": 0, "place_name": "บ้าน", "latitude": HOME[0],
     "longitude": HOME[1], "visit_frequency": 100, "avg_stay_time": 28800.0,
     "radius_m": 150, "source": "manual"},
]


async def _make(db_session, severity_level, with_pins=True):
    caregiver = await crud.create_user(
        db_session, firebase_uid="uid-cg", name="ผู้ดูแล", role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid="uid-pt", name="ยาย", role="patient",
        caregiver_id=caregiver.id, severity_level=severity_level)
    await db_session.flush()
    if with_pins:
        await crud.upsert_behavioral_profile(
            db_session, patient.id,
            known_places=json.dumps(PINS, ensure_ascii=False))
    await db_session.commit()
    return {"patient": patient.id, "caregiver": caregiver.id}


@pytest.fixture
async def level2(db_session):
    return await _make(db_session, severity_level=2)


@pytest.fixture
async def level1(db_session):
    return await _make(db_session, severity_level=1)


async def ask(client, patient_id, metres=250, name="ตลาดใหม่"):
    lat, lng = north(metres)
    return await client.post("/api/trip-requests", json={
        "patient_id": patient_id, "destination_name": name,
        "latitude": lat, "longitude": lng})


# ── asking ───────────────────────────────────────────────────────────────────

async def test_level2_request_is_stored_pending_with_a_usable_confidence(
        client, level2, db_session):
    r = await ask(client, level2["patient"])
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["status"] == "pending"
    assert body["destination_name"] == "ตลาดใหม่"
    # The whole reason trip_confidence exists: score_place caps at 0.350 here.
    assert body["confidence"] > 0.35
    assert body["nearest_place_name"] == "บ้าน"
    assert body["factors"]["danger_zone"] is False

    row = await db_session.get(TripRequest, body["id"])
    assert row.status == "pending"
    assert row.decided_at is None


async def test_level1_needs_no_approval_and_leaves_no_row(client, level1, db_session):
    r = await ask(client, level1["patient"])
    assert r.status_code == 201
    assert r.json()["status"] == "not_required"
    assert r.json()["id"] is None
    assert (await db_session.execute(select(TripRequest))).scalars().all() == []


async def test_unknown_patient_is_404(client):
    r = await ask(client, 9999)
    assert r.status_code == 404


async def test_confidence_is_frozen_on_the_row(client, level2, db_session):
    """The caregiver decides about the moment the patient asked."""
    body = (await ask(client, level2["patient"])).json()
    row = await db_session.get(TripRequest, body["id"])
    assert row.confidence == body["confidence"]
    assert json.loads(row.factors)["nearest_place_name"] == "บ้าน"


async def test_a_destination_in_a_danger_zone_scores_zero_and_names_it(
        client, level2, db_session):
    from app.db.models import DangerZone

    dest = north(250)
    db_session.add(DangerZone(
        name="คลอง", center_latitude=dest[0], center_longitude=dest[1],
        radius_meters=200, zone_type="water", active=True,
        source_reference="test", rationale="drowning risk", created_by="test"))
    await db_session.commit()

    body = (await ask(client, level2["patient"], metres=250)).json()
    assert body["confidence"] == 0.0
    assert body["factors"]["danger_zone"] is True
    assert body["blocking_zone_name"] == "คลอง"


async def test_no_pins_says_no_profile_rather_than_scoring_zero(client, db_session):
    people = await _make(db_session, severity_level=2, with_pins=False)
    body = (await ask(client, people["patient"])).json()
    assert body["confidence_status"] == "no_profile"


# ── the caregiver's list ─────────────────────────────────────────────────────

async def test_caregiver_sees_pending_requests_newest_first(client, level2):
    await ask(client, level2["patient"], name="ตลาด")
    await ask(client, level2["patient"], name="วัดใหม่")

    r = await client.get(f"/api/patients/{level2['patient']}/trip-requests")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["requests"][0]["destination_name"] == "วัดใหม่"


async def test_the_list_can_be_filtered_to_pending(client, level2):
    first = (await ask(client, level2["patient"], name="ตลาด")).json()
    await ask(client, level2["patient"], name="วัดใหม่")
    await client.patch(f"/api/trip-requests/{first['id']}",
                       json={"decision": "approve"})

    pending = await client.get(
        f"/api/patients/{level2['patient']}/trip-requests?status=pending")
    assert pending.json()["count"] == 1
    assert pending.json()["requests"][0]["destination_name"] == "วัดใหม่"


async def test_listing_an_unknown_patient_is_404(client):
    assert (await client.get("/api/patients/9999/trip-requests")).status_code == 404


# ── deciding ─────────────────────────────────────────────────────────────────

async def test_approving_raises_no_alert(client, level2, db_session):
    body = (await ask(client, level2["patient"])).json()
    r = await client.patch(f"/api/trip-requests/{body['id']}",
                           json={"decision": "approve"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["alert_id"] is None
    assert (await db_session.execute(select(Alert))).scalars().all() == []


async def test_rejecting_writes_a_trip_denied_alert_not_an_sos(
        client, level2, db_session):
    """Sharing ``sos`` would let this suppress a real button press for 10 min."""
    body = (await ask(client, level2["patient"])).json()
    r = await client.patch(f"/api/trip-requests/{body['id']}",
                           json={"decision": "reject"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    alert = await db_session.get(Alert, r.json()["alert_id"])
    assert alert.alert_type == "trip_denied"
    assert alert.alert_type != "sos"
    assert alert.severity == "high"
    assert "ตลาดใหม่" in alert.message
    # Somewhere to look, if the patient goes anyway.
    assert alert.latitude == pytest.approx(body["latitude"])


async def test_deciding_twice_is_refused(client, level2):
    body = (await ask(client, level2["patient"])).json()
    first = await client.patch(f"/api/trip-requests/{body['id']}",
                               json={"decision": "approve"})
    assert first.status_code == 200
    second = await client.patch(f"/api/trip-requests/{body['id']}",
                                json={"decision": "reject"})
    assert second.status_code == 409


async def test_deciding_an_unknown_request_is_404(client):
    r = await client.patch("/api/trip-requests/9999", json={"decision": "approve"})
    assert r.status_code == 404


async def test_a_decision_must_be_approve_or_reject(client, level2):
    body = (await ask(client, level2["patient"])).json()
    r = await client.patch(f"/api/trip-requests/{body['id']}",
                           json={"decision": "maybe"})
    assert r.status_code == 422


async def test_the_caregiver_who_rejects_is_not_pushed_their_own_decision(
        client, level2, db_session, monkeypatch):
    """Report line 535 says notify every caregiver. Today that set is one person."""
    body = (await ask(client, level2["patient"])).json()

    tokens = {"tok-cg": "uid-cg"}
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: tokens[t])

    r = await client.patch(f"/api/trip-requests/{body['id']}",
                           json={"decision": "reject"},
                           headers={"Authorization": "Bearer tok-cg"})
    assert r.status_code == 200
    assert r.json()["push"] == "skipped_self"
    # The row still exists — the timeline and the dashboard both read it.
    assert await db_session.get(Alert, r.json()["alert_id"]) is not None


async def test_a_second_caregiver_turns_the_self_skip_off(
        client, level2, db_session, monkeypatch):
    """The skip is whole-list equality, so it only fires for a lone caregiver.

    Written 2026-08-28 because the module docstring described this rule wrongly
    twice, in opposite directions — first "the others get notified for free",
    then "the push is skipped for all of them". Neither survived being run. With
    a second caregiver linked, the decider is no longer the whole list, the skip
    does not apply, and the push is attempted for everybody (no device tokens
    exist here, so that surfaces as ``no_caregiver`` — the point is that it is
    not ``skipped_self``).
    """
    second = await crud.create_user(
        db_session, firebase_uid="uid-cg2", name="ผู้ดูแลคนที่สอง",
        role="caregiver")
    await db_session.flush()
    await crud.link_caregiver(db_session, level2["patient"], second.id)
    await db_session.commit()

    body = (await ask(client, level2["patient"])).json()

    tokens = {"tok-cg": "uid-cg"}
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: tokens[t])

    r = await client.patch(f"/api/trip-requests/{body['id']}",
                           json={"decision": "reject"},
                           headers={"Authorization": "Bearer tok-cg"})
    assert r.status_code == 200
    assert r.json()["push"] != "skipped_self"
