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


# ── where the caregiver is ───────────────────────────────────────────────────
#
# Note this endpoint is scoped by {caregiver_id}, not {patient_id}, so
# ``test_every_patient_scoped_route_is_guarded`` does not walk it. Its 403 is
# covered by hand below.

async def test_a_caregiver_reports_their_position_and_it_overwrites(
        client, db_session, household):
    """Overwrite, not append. Nothing keeps a trail of a family member who is
    not the patient — ranking only ever asks where they are now."""
    first = await client.put(
        f"/api/caregivers/{household['primary']}/location",
        json={"latitude": 13.7563, "longitude": 100.5018})
    assert first.status_code == 200, first.text

    second = await client.put(
        f"/api/caregivers/{household['primary']}/location",
        json={"latitude": 13.8000, "longitude": 100.6000})
    assert second.status_code == 200

    db_session.expire_all()
    user = await crud.get_user(db_session, household["primary"])
    assert (user.last_latitude, user.last_longitude) == (13.8000, 100.6000)
    assert user.location_updated_at is not None


async def test_a_caregiver_cannot_report_someone_elses_position(
        client, db_session, household, auth_on, monkeypatch):
    """Writing another caregiver's location would put them at the top of a
    distance ranking for a patient they are nowhere near."""
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)

    resp = await client.put(
        f"/api/caregivers/{household['primary']}/location",
        json={"latitude": 13.7563, "longitude": 100.5018},
        headers=bearer("tok"))

    assert resp.status_code == 403


async def test_a_patient_position_is_refused_here(client, household):
    """It belongs in POST /api/gps, which smooths it, scores it and can raise an
    alert. Accepted here it would land somewhere none of that happens and look
    like it worked."""
    resp = await client.put(
        f"/api/caregivers/{household['patient']}/location",
        json={"latitude": 13.7563, "longitude": 100.5018})

    assert resp.status_code == 422
    assert "POST /api/gps" in resp.json()["detail"]


async def test_a_caregiver_who_has_never_reported_has_no_position(
        db_session, household):
    """Null, not (0, 0) — the Gulf of Guinea is not a default."""
    user = await crud.get_user(db_session, household["second"])

    assert user.last_latitude is None
    assert user.last_longitude is None
    assert user.location_updated_at is None


# ── how the second caregiver gets in ─────────────────────────────────────────

async def _invite(client, patient_id, **kw):
    return await client.post(
        f"/api/patients/{patient_id}/caregiver-invites", json={}, **kw)


async def test_an_invite_links_the_second_caregiver(client, db_session, household):
    """The whole point: before this, patient_caregivers could hold several
    people and nothing could put a second one in it."""
    issued = await _invite(client, household["patient"])
    assert issued.status_code == 201, issued.text

    redeemed = await client.post("/api/caregivers/redeem-invite", json={
        "code": issued.json()["invite_code"],
        "caregiver_id": household["second"]})

    assert redeemed.status_code == 200, redeemed.text
    body = redeemed.json()
    assert body["patient_id"] == household["patient"]
    assert body["patient_name"] == "คุณยาย"
    assert body["already_linked"] is False
    assert await crud.get_caregiver_ids(db_session, household["patient"])         == [household["primary"], household["second"]]


async def test_the_second_caregiver_can_then_read_the_patient(
        client, db_session, household, auth_on, monkeypatch):
    """End to end with auth on, which is the only configuration this matters in.

    Note the redeem call carries no ``caregiver_id``: with auth on it is taken
    from the token, so a caregiver cannot redeem an invite into someone else's
    account.
    """
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: "uid-primary")
    issued = await _invite(client, household["patient"], headers=bearer("tok"))
    assert issued.status_code == 201, issued.text

    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)
    redeemed = await client.post(
        "/api/caregivers/redeem-invite",
        json={"code": issued.json()["invite_code"]}, headers=bearer("tok"))
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["caregiver_id"] == household["second"]

    resp = await client.get(
        f"/api/patients/{household['patient']}/alerts", headers=bearer("tok"))

    assert resp.status_code == 200, resp.text


async def test_an_invite_is_single_use(client, household):
    """A code that survives redemption can be forwarded to somebody the family
    never meant to let in."""
    issued = await _invite(client, household["patient"])
    code = issued.json()["invite_code"]
    first = await client.post("/api/caregivers/redeem-invite", json={
        "code": code, "caregiver_id": household["second"]})
    assert first.status_code == 200

    second = await client.post("/api/caregivers/redeem-invite", json={
        "code": code, "caregiver_id": household["stranger"]})

    assert second.status_code == 404
    assert second.json()["detail"] ==         "invalid, expired, or already-used invite code"


async def test_redeeming_when_already_linked_still_spends_the_code(
        client, db_session, household):
    """Otherwise the code stays live in the hands of someone who no longer needs
    it, and can be passed to someone who was never invited."""
    issued = await _invite(client, household["patient"])
    resp = await client.post("/api/caregivers/redeem-invite", json={
        "code": issued.json()["invite_code"],
        "caregiver_id": household["primary"]})

    assert resp.status_code == 200
    assert resp.json()["already_linked"] is True
    assert len(await crud.get_caregiver_ids(db_session, household["patient"])) == 1

    again = await client.post("/api/caregivers/redeem-invite", json={
        "code": issued.json()["invite_code"],
        "caregiver_id": household["second"]})
    assert again.status_code == 404


async def test_a_pairing_code_cannot_be_redeemed_as_an_invite(
        client, db_session, household):
    """The reason caregiver_invites is a separate table. A code meant to sign a
    phone in AS the patient must never grant a caregiver's view OF them."""
    created = await client.post("/api/patients", json={
        "name": "ยายอีกคน", "severity_level": 1,
        "caregiver_id": household["primary"]})
    pairing_code = created.json()["pairing_code"]

    resp = await client.post("/api/caregivers/redeem-invite", json={
        "code": pairing_code, "caregiver_id": household["second"]})

    assert resp.status_code == 404


async def test_a_patient_cannot_invite_a_caregiver_to_themselves(
        client, db_session, household, auth_on, monkeypatch):
    """verify_patient_access lets the patient through — correct for reading
    their own data, wrong for handing out access to it. A person with dementia
    giving away their own live position is what the caregiver is there to
    prevent."""
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: "uid-patient-2")

    resp = await _invite(client, household["patient"], headers=bearer("tok"))

    assert resp.status_code == 403
    assert "caregiver of this patient" in resp.json()["detail"]


async def test_an_unrelated_caregiver_cannot_issue_an_invite(
        client, household, auth_on, monkeypatch):
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: "uid-stranger-2")

    resp = await _invite(client, household["patient"], headers=bearer("tok"))

    assert resp.status_code == 403


async def test_a_patient_device_cannot_redeem_an_invite(client, household):
    """Role is the only thing separating the two kinds of account here."""
    issued = await _invite(client, household["patient"])

    resp = await client.post("/api/caregivers/redeem-invite", json={
        "code": issued.json()["invite_code"],
        "caregiver_id": household["patient"]})

    assert resp.status_code == 422
    assert "not a caregiver" in resp.json()["detail"]


async def test_a_typed_invite_code_is_forgiving_of_case_and_separator(
        client, household):
    """Same rule as pairing codes — a family reads these aloud off one screen
    and types them into another."""
    issued = await _invite(client, household["patient"])
    typed = issued.json()["invite_code"].lower().replace("-", " ") + " "

    resp = await client.post("/api/caregivers/redeem-invite", json={
        "code": typed, "caregiver_id": household["second"]})

    assert resp.status_code == 200, resp.text
