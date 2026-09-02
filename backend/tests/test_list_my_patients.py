"""``GET /api/patients`` — the list a caregiver app rebuilds on every launch.

Nothing could ask this direction before. ``crud.get_caregiver_ids`` goes patient
→ caregivers and no route went the other way, so the Flutter caregiver screen
held its patients in a plain ``List`` in memory
(``caregiver_homepage_screen.dart:21``, no ``initState`` load) and lost them on
restart — taking the track screen, the alert feed and every per-patient call
with them. Same shape as the hardcoded ``CAREGIVER_TEST_ID``: it looks like a
shortcut on their side and it was the only thing we had left them.

The load-bearing tests are ``test_it_answers_while_auth_is_switched_off`` and
``test_a_caregiver_never_sees_someone_elses_patients``. The first is why this
route uses ``signed_in_caller``; the second is what that buys.

Firebase is never called — ``verify_firebase_token`` is replaced with a fixed
token→uid map, same as ``test_auth.py`` and ``test_who_am_i.py``.
"""
from __future__ import annotations

import pytest

from app.db import crud
from app.services import auth

pytestmark = pytest.mark.asyncio

ALICE_UID = "uid-caregiver-alice"
BOB_UID = "uid-caregiver-bob"
LONER_UID = "uid-caregiver-no-patients"

_TOKENS = {
    "tok-alice": ALICE_UID,
    "tok-bob": BOB_UID,
    "tok-loner": LONER_UID,
}


@pytest.fixture
def firebase_stub(monkeypatch):
    """Stub the signature check and leave ``AUTH_ENABLED`` alone (i.e. off)."""
    def _verify(token: str) -> str:
        if token not in _TOKENS:
            raise auth._unauthorized("invalid or expired ID token")
        return _TOKENS[token]

    monkeypatch.setattr(auth, "verify_firebase_token", _verify)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def household(db_session):
    """Two caregivers with their own patients, plus one who shares Alice's."""
    alice = await crud.create_user(
        db_session, firebase_uid=ALICE_UID, name="อลิซ", role="caregiver")
    bob = await crud.create_user(
        db_session, firebase_uid=BOB_UID, name="บ็อบ", role="caregiver")
    await crud.create_user(
        db_session, firebase_uid=LONER_UID, name="คนไม่มีคนไข้", role="caregiver")
    await db_session.flush()

    # create_user writes the patient_caregivers link itself, is_primary=True.
    yai = await crud.create_user(
        db_session, firebase_uid="uid-yai", name="ยาย", role="patient",
        caregiver_id=alice.id, severity_level=2)
    ta = await crud.create_user(
        db_session, firebase_uid="uid-ta", name="ตา", role="patient",
        caregiver_id=alice.id)
    bobs = await crud.create_user(
        db_session, firebase_uid="uid-bobs", name="แม่ของบ็อบ", role="patient",
        caregiver_id=bob.id)
    await db_session.flush()

    # Bob is invited onto Alice's ยาย as a second caregiver: same access, not primary.
    await crud.link_caregiver(db_session, patient_id=yai.id, caregiver_id=bob.id)
    await db_session.commit()
    return {"alice": alice.id, "bob": bob.id,
            "yai": yai.id, "ta": ta.id, "bobs_mother": bobs.id}


async def test_it_answers_while_auth_is_switched_off(client, household, firebase_stub):
    """Why this route uses ``signed_in_caller``, like ``/api/me``.

    ``AUTH_ENABLED`` is off here — the suite's default and the pilot's. Built on
    ``current_caller`` the caller would be ANONYMOUS, and the route would have
    to return either everybody's patients or nobody's: a privacy hole, or a
    screen that is empty in exactly the mode the pilot runs in.
    """
    assert auth.AUTH_ENABLED is False

    resp = await client.get("/api/patients", headers=bearer("tok-alice"))

    assert resp.status_code == 200
    assert resp.json()["count"] == 2


async def test_a_caregiver_never_sees_someone_elses_patients(
    client, household, firebase_stub
):
    body = (await client.get("/api/patients", headers=bearer("tok-alice"))).json()
    ids = {p["patient_id"] for p in body["patients"]}

    assert ids == {household["yai"], household["ta"]}
    assert household["bobs_mother"] not in ids


async def test_a_second_caregiver_sees_the_patient_they_were_invited_to(
    client, household, firebase_stub
):
    """Every link grants identical access — is_primary is not a permission."""
    body = (await client.get("/api/patients", headers=bearer("tok-bob"))).json()
    by_id = {p["patient_id"]: p for p in body["patients"]}

    assert set(by_id) == {household["bobs_mother"], household["yai"]}
    assert by_id[household["bobs_mother"]]["is_primary"] is True   # Bob created her
    assert by_id[household["yai"]]["is_primary"] is False          # invited onto her


async def test_no_patients_is_an_empty_list_not_a_404(client, household, firebase_stub):
    """"Nobody yet" is a true answer; a 404 would read as "your account is missing"."""
    resp = await client.get("/api/patients", headers=bearer("tok-loner"))

    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "patients": []}


async def test_carries_name_and_severity_level(client, household, firebase_stub):
    # The caregiver home screen shows both, and severity_level decides which
    # patient interface the phone draws.
    body = (await client.get("/api/patients", headers=bearer("tok-alice"))).json()
    yai = next(p for p in body["patients"] if p["patient_id"] == household["yai"])

    assert yai["name"] == "ยาย"
    assert yai["severity_level"] == 2


async def test_unset_severity_level_is_null_not_defaulted(
    client, household, firebase_stub
):
    """Never default to 1 — the app must ask the caregiver instead of guessing.

    Guessing it wrong sends a mid-stage patient out to cross a road: the stage
    decides the search radius *and* where the SOS button leads.
    """
    body = (await client.get("/api/patients", headers=bearer("tok-alice"))).json()
    ta = next(p for p in body["patients"] if p["patient_id"] == household["ta"])

    assert ta["severity_level"] is None


async def test_no_token_is_401_even_with_auth_off(client, household, firebase_stub):
    resp = await client.get("/api/patients")

    assert resp.status_code == 401


async def test_a_bad_token_is_401(client, household, firebase_stub):
    resp = await client.get("/api/patients", headers=bearer("tok-forged"))

    assert resp.status_code == 401


async def test_order_is_stable_across_calls(client, household, firebase_stub):
    """A list that reorders itself between launches is a list you cannot trust."""
    first = (await client.get("/api/patients", headers=bearer("tok-alice"))).json()
    second = (await client.get("/api/patients", headers=bearer("tok-alice"))).json()

    assert [p["patient_id"] for p in first["patients"]] == \
           [p["patient_id"] for p in second["patients"]]
