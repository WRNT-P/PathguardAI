"""``PUT /api/caregivers/{id}/availability`` — ว่าง/ไม่ว่าง, and the third state.

The patient's SOS contact screen (``sos_contact_screen.dart``) was already
showing ว่าง/ไม่ว่าง beside each caregiver. The value was hardcoded in the Dart
source: nothing in the schema held an answer, so a person in trouble was being
told who to call by a constant.

The load-bearing tests here are the ``None`` ones. Availability is nullable and
that is not laziness — "has never answered" is a different claim from "said no",
and collapsing them puts ไม่ว่าง next to a caregiver who would have come. Every
default on offer lies in one direction or the other, so the endpoint refuses to
invent one and the app is told to render null as unknown.

Firebase is never called; the auth-on cases reuse ``test_auth``'s fixtures.
"""
from __future__ import annotations

import pytest

from app.db import crud
from app.services import auth
from tests.test_auth import auth_on, bearer  # noqa: F401  (fixture import)

pytestmark = pytest.mark.asyncio

SECOND_UID = "uid-second-availability"


@pytest.fixture
async def household(db_session):
    """A patient, the caregiver who created them, and a second caregiver."""
    primary = await crud.create_user(
        db_session, firebase_uid="uid-primary-avail", name="ลูกสาว",
        role="caregiver", phone="0812345678")
    second = await crud.create_user(
        db_session, firebase_uid=SECOND_UID, name="ลูกชาย", role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid="uid-patient-avail", name="คุณยาย",
        role="patient", caregiver_id=primary.id)
    await crud.link_caregiver(db_session, patient.id, second.id)
    await db_session.commit()
    return {"patient": patient.id, "primary": primary.id, "second": second.id}


# ── setting it ───────────────────────────────────────────────────────────────

async def test_a_caregiver_sets_themselves_available(client, db_session, household):
    resp = await client.put(
        f"/api/caregivers/{household['primary']}/availability",
        json={"is_available": True})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"caregiver_id": household["primary"],
                           "is_available": True}

    db_session.expire_all()
    user = await crud.get_user(db_session, household["primary"])
    assert user.is_available is True


async def test_setting_it_false_stores_false_not_null(
        client, db_session, household):
    """The distinction the whole column exists for. ``False`` is a statement the
    caregiver made; ``None`` is the absence of one, and a writer that stored
    "not available" as a null would erase the difference on the way in."""
    await client.put(f"/api/caregivers/{household['primary']}/availability",
                     json={"is_available": True})
    resp = await client.put(
        f"/api/caregivers/{household['primary']}/availability",
        json={"is_available": False})

    assert resp.status_code == 200
    assert resp.json()["is_available"] is False

    db_session.expire_all()
    user = await crud.get_user(db_session, household["primary"])
    assert user.is_available is False
    assert user.is_available is not None


async def test_a_caregiver_who_has_never_answered_is_null(db_session, household):
    """Not False. Nobody has asked this person anything yet."""
    user = await crud.get_user(db_session, household["second"])

    assert user.is_available is None


async def test_it_writes_nothing_but_the_flag(client, db_session, household):
    """A caregiver going out for the evening is not an event about the patient.

    The endpoint must not touch GPS, risk or alerts — and it must not touch
    ``location_updated_at`` either, which sits two lines away in the same model
    and would make a stale position look fresh enough to win a ranking.
    """
    assert await crud.get_latest_gps(db_session, household["patient"]) is None

    await client.put(f"/api/caregivers/{household['primary']}/availability",
                     json={"is_available": False})

    db_session.expire_all()
    user = await crud.get_user(db_session, household["primary"])
    assert user.location_updated_at is None
    assert user.last_latitude is None
    assert await crud.get_latest_gps(db_session, household["patient"]) is None


# ── who may set it ───────────────────────────────────────────────────────────

async def test_a_caregiver_cannot_set_someone_elses_availability(
        client, household, auth_on, monkeypatch):
    """Marking somebody else busy takes them off the top of the SOS screen of a
    patient who may be about to need them, and they would never know."""
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)

    resp = await client.put(
        f"/api/caregivers/{household['primary']}/availability",
        json={"is_available": False}, headers=bearer("tok"))

    assert resp.status_code == 403


async def test_a_caregiver_sets_their_own_with_auth_on(
        client, household, auth_on, monkeypatch):
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)

    resp = await client.put(
        f"/api/caregivers/{household['second']}/availability",
        json={"is_available": True}, headers=bearer("tok"))

    assert resp.status_code == 200
    assert resp.json()["is_available"] is True


async def test_a_patient_has_no_availability(client, household):
    """Nothing reads one for a patient, so accepting it would write a column no
    screen will ever show while looking to the caller like it worked."""
    resp = await client.put(
        f"/api/caregivers/{household['patient']}/availability",
        json={"is_available": True})

    assert resp.status_code == 422
    assert "caregiver" in resp.json()["detail"]


async def test_an_unknown_user_is_a_404(client, household):
    resp = await client.put("/api/caregivers/999999/availability",
                            json={"is_available": True})

    assert resp.status_code == 404


async def test_the_body_requires_a_boolean(client, household):
    """There is no way to send ``None`` through this route on purpose — "never
    answered" is only ever the absence of a call."""
    resp = await client.put(
        f"/api/caregivers/{household['primary']}/availability",
        json={"is_available": None})

    assert resp.status_code == 422


# ── reading it back on the screen that needs it ──────────────────────────────

async def test_the_sos_screen_can_read_it(client, household):
    """``GET /api/patients/{id}/caregivers`` is what the SOS screen calls. A
    value that can be set and not read is the same as no value at all."""
    await client.put(f"/api/caregivers/{household['primary']}/availability",
                     json={"is_available": True})

    resp = await client.get(f"/api/patients/{household['patient']}/caregivers")

    assert resp.status_code == 200, resp.text
    by_id = {c["caregiver_id"]: c for c in resp.json()["caregivers"]}
    assert by_id[household["primary"]]["is_available"] is True
    # The one who never answered stays null all the way to the app.
    assert by_id[household["second"]]["is_available"] is None


async def test_being_unavailable_does_not_remove_anyone_from_the_list(
        client, household):
    """Same rule as a stale position: demote, never filter. A busy caregiver is
    still a person standing next to the patient, and an SOS screen that answers
    "nobody" while someone is in trouble is worse than one in the wrong order.
    """
    await client.put(f"/api/caregivers/{household['primary']}/availability",
                     json={"is_available": False})
    await client.put(f"/api/caregivers/{household['second']}/availability",
                     json={"is_available": False})

    resp = await client.get(f"/api/patients/{household['patient']}/caregivers")

    ids = {c["caregiver_id"] for c in resp.json()["caregivers"]}
    assert ids == {household["primary"], household["second"]}
