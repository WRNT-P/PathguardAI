"""A patient can have more than one caregiver (2026-08-28).

``users.caregiver_id`` was a single nullable FK, so three things the report has
always promised were not merely unbuilt but unbuildable: alerting every
caregiver, ranking them by distance, and one of them claiming "I'll go and get
them". The app side confirmed on 2026-08-28 that they want all three and are
waiting on the schema.

This file covers the half that has security consequences. The link is what
``assert_may_access_patient`` reads, so a second caregiver being linked is
exactly a second account gaining the right to read a dementia patient's live
position — and a *stranger* staying 403 is the thing that must not regress
while that door is being widened.
"""
from __future__ import annotations

import pytest

from app.db import crud
from app.services import auth
from tests.test_auth import auth_on, bearer  # noqa: F401  (fixture import)

pytestmark = pytest.mark.asyncio

SECOND_UID = "uid-second-caregiver"


@pytest.fixture
async def household(db_session, monkeypatch):
    """A patient, the caregiver who created them, a second caregiver, a stranger."""
    primary = await crud.create_user(
        db_session, firebase_uid="uid-primary", name="ลูกสาว", role="caregiver")
    second = await crud.create_user(
        db_session, firebase_uid=SECOND_UID, name="ลูกชาย", role="caregiver")
    stranger = await crud.create_user(
        db_session, firebase_uid="uid-stranger-2", name="คนอื่น", role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid="uid-patient-2", name="คุณยาย", role="patient",
        caregiver_id=primary.id)
    await db_session.commit()
    return {"patient": patient.id, "primary": primary.id,
            "second": second.id, "stranger": stranger.id}


async def test_creating_a_patient_makes_that_caregiver_the_primary(
        db_session, household):
    assert await crud.get_caregiver_ids(db_session, household["patient"]) \
        == [household["primary"]]
    assert await crud.get_caregiver_id(db_session, household["patient"]) \
        == household["primary"]


async def test_a_second_caregiver_joins_and_the_primary_stays_first(
        db_session, household):
    """Order is not incidental — ``get_caregiver_id`` answers "who is *the*
    caregiver" off the front of this list, and a distance tie is broken by it."""
    await crud.link_caregiver(
        db_session, household["patient"], household["second"])
    await db_session.commit()

    assert await crud.get_caregiver_ids(db_session, household["patient"]) \
        == [household["primary"], household["second"]]
    assert await crud.get_caregiver_id(db_session, household["patient"]) \
        == household["primary"]


async def test_linking_the_same_caregiver_twice_is_a_no_op(db_session, household):
    """Not tidiness: a duplicate row is a duplicate push and a second entry in
    the distance ranking for one person."""
    assert await crud.link_caregiver(
        db_session, household["patient"], household["second"]) is not None
    assert await crud.link_caregiver(
        db_session, household["patient"], household["second"]) is None
    await db_session.commit()

    assert len(await crud.get_caregiver_ids(db_session, household["patient"])) == 2


async def test_an_alert_reaches_the_devices_of_every_caregiver(
        db_session, household):
    """The push fan-out is this function. One caregiver with two phones and two
    caregivers with one each both have to come back as two tokens."""
    await crud.link_caregiver(
        db_session, household["patient"], household["second"])
    await crud.upsert_device_token(
        db_session, household["primary"], "token-primary", "android")
    await crud.upsert_device_token(
        db_session, household["second"], "token-second", "android")
    await crud.upsert_device_token(
        db_session, household["stranger"], "token-stranger", "android")
    await db_session.commit()

    tokens = await crud.get_caregiver_tokens(db_session, household["patient"])

    assert set(tokens) == {"token-primary", "token-second"}


# ── the authorization half ───────────────────────────────────────────────────

async def test_the_second_caregiver_may_read_the_patient(
        client, db_session, household, auth_on, monkeypatch):
    """The point of the whole change: before this, only the primary got in."""
    await crud.link_caregiver(
        db_session, household["patient"], household["second"])
    await db_session.commit()
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)

    resp = await client.get(
        f"/api/patients/{household['patient']}/alerts", headers=bearer("tok"))

    assert resp.status_code == 200, resp.text


async def test_an_unlinked_caregiver_is_still_403(
        client, db_session, household, auth_on, monkeypatch):
    """The door got wider, not open. A caregiver of somebody else's patient must
    not be let in by "is a caregiver" — only by being linked to *this* patient."""
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: "uid-stranger-2")

    resp = await client.get(
        f"/api/patients/{household['patient']}/alerts", headers=bearer("tok"))

    assert resp.status_code == 403


async def test_a_patient_with_no_caregiver_notifies_nobody(db_session):
    """A patient created through /api/register with no caregiver_id. Must be an
    empty list, never every token in the table."""
    patient = await crud.create_user(
        db_session, firebase_uid="uid-lonely", name="ไม่มีผู้ดูแล", role="patient")
    await db_session.commit()

    assert await crud.get_caregiver_ids(db_session, patient.id) == []
    assert await crud.get_caregiver_tokens(db_session, patient.id) == []
