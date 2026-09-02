"""users.phone — the number the patient's SOS screen dials, 2026-08-30.

The app side wired the Call button on the SOS contact screen and had to
hardcode a number into the Dart source, because nothing in the schema held one.
``users`` had id, firebase_uid, name, role, severity_level, the three location
columns and created_at — no way to ask the database who to call.

Two things this file holds down, and the second matters more than the first:
the number reaches the screen that dials it, and **an unset number arrives as
``null`` rather than as anything dialable**. A wrong number on that screen
fails at the one moment it exists for.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import crud

pytestmark = pytest.mark.asyncio

HOME = (13.7563, 100.5018)


async def test_register_keeps_the_phone_and_hands_it_back(client):
    body = (await client.post("/api/register", json={
        "firebase_uid": "uid-phone-register",
        "name": "ลูกสาว",
        "role": "caregiver",
        "phone": "081-234-5678",
    })).json()

    assert body["phone"] == "081-234-5678"


async def test_register_without_a_phone_stores_null(client):
    body = (await client.post("/api/register", json={
        "firebase_uid": "uid-phone-absent",
        "name": "ลูกชาย",
        "role": "caregiver",
    })).json()

    assert body["phone"] is None


async def test_the_sos_screen_can_read_a_real_number(client, db_session):
    """The whole point: `GET .../caregivers` is what the SOS screen lists."""
    caregiver = await crud.create_user(
        db_session, firebase_uid="uid-phone-ranked", name="ลูกสาว",
        role="caregiver", phone="02-123-4567")
    no_number = await crud.create_user(
        db_session, firebase_uid="uid-phone-ranked-2", name="ลูกชาย",
        role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid="uid-phone-patient", name="คุณยาย",
        role="patient", caregiver_id=caregiver.id)
    await db_session.flush()
    await crud.link_caregiver(db_session, patient.id, no_number.id)
    await crud.save_gps_point(
        db_session, patient_id=patient.id, latitude=HOME[0], longitude=HOME[1],
        recorded_at=datetime.now(timezone.utc))
    await db_session.commit()

    rows = (await client.get(
        f"/api/patients/{patient.id}/caregivers")).json()["caregivers"]
    by_id = {c["caregiver_id"]: c for c in rows}

    assert by_id[caregiver.id]["phone"] == "02-123-4567"
    # Null, not "" and not a placeholder: the contract tells the app to hide the
    # Call button, and anything dialable here would be dialed.
    assert by_id[no_number.id]["phone"] is None
