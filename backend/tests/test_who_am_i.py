"""``GET /api/me`` — the route that stops the app guessing its own ``users.id``.

The app had no way to ask. ``POST /api/register`` is the only other route that
ever returns a ``users.id`` and it fires once, at sign-up, so a caregiver who
signed in again — or reinstalled — was stranded. What the Flutter app actually
did was read a hardcoded ``CAREGIVER_TEST_ID`` out of ``.env``
(``caregiver_login_screen.dart:110``), which makes every returning caregiver the
test account.

The load-bearing test here is ``test_it_answers_while_auth_is_switched_off``.
Every other dependency in ``auth.py`` returns ``ANONYMOUS`` when the switch is
off; if this one did too it would answer ``id: null`` in exactly the mode the
pilot runs in, and the ``.env`` hack would have to stay.

Firebase is never called — ``verify_firebase_token`` is replaced with a fixed
token→uid map, same as ``test_auth.py``. What is under test is everything after
a good signature.
"""
from __future__ import annotations

import pytest

from app.db import crud
from app.services import auth

pytestmark = pytest.mark.asyncio

CAREGIVER_UID = "uid-caregiver-me"
PATIENT_UID = "uid-patient-me"
UNREGISTERED_UID = "uid-signed-in-never-registered"

_TOKENS = {
    "tok-caregiver": CAREGIVER_UID,
    "tok-patient": PATIENT_UID,
    "tok-unregistered": UNREGISTERED_UID,
}


@pytest.fixture
def firebase_stub(monkeypatch):
    """Stub the signature check and leave ``AUTH_ENABLED`` alone (i.e. off).

    Deliberately does not touch the flag: the suite's default is the pilot's
    default, and this route has to work there.
    """
    def _verify(token: str) -> str:
        if token not in _TOKENS:
            raise auth._unauthorized("invalid or expired ID token")
        return _TOKENS[token]

    monkeypatch.setattr(auth, "verify_firebase_token", _verify)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def people(db_session):
    caregiver = await crud.create_user(
        db_session, firebase_uid=CAREGIVER_UID, name="ผู้ดูแลทดสอบ",
        role="caregiver", phone="0812345678")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid=PATIENT_UID, name="คนไข้ทดสอบ",
        role="patient", caregiver_id=caregiver.id)
    await db_session.commit()
    return {"caregiver": caregiver.id, "patient": patient.id}


async def test_it_answers_while_auth_is_switched_off(client, people, firebase_stub):
    """The whole point. ``AUTH_ENABLED`` is off here — the suite's default."""
    assert auth.AUTH_ENABLED is False

    resp = await client.get("/api/me", headers=bearer("tok-caregiver"))

    assert resp.status_code == 200
    assert resp.json()["id"] == people["caregiver"]


async def test_it_returns_the_caregivers_own_row(client, people, firebase_stub):
    resp = await client.get("/api/me", headers=bearer("tok-caregiver"))

    body = resp.json()
    assert body["id"] == people["caregiver"]
    assert body["name"] == "ผู้ดูแลทดสอบ"
    assert body["role"] == "caregiver"
    # The SOS screen dials this; without it the app has an id and no way to call.
    assert body["phone"] == "0812345678"
    assert body["firebase_uid"] == CAREGIVER_UID


async def test_a_patient_token_is_answered_as_a_patient(client, people, firebase_stub):
    """Not a 403. The caregiver screen has to be able to tell the difference, and
    a patient device legitimately needs its own id too — so the honest answer is
    the role, not a refusal."""
    resp = await client.get("/api/me", headers=bearer("tok-patient"))

    assert resp.status_code == 200
    assert resp.json()["id"] == people["patient"]
    assert resp.json()["role"] == "patient"


async def test_no_token_is_rejected(client, people, firebase_stub):
    resp = await client.get("/api/me")

    assert resp.status_code == 401
    assert "bearer" in resp.json()["detail"].lower()


async def test_an_empty_bearer_is_rejected(client, people, firebase_stub):
    """``api_client.dart`` sends ``Bearer ${token ?? ''}`` — a signed-out app
    sends the header with nothing behind it, so the empty case is real traffic
    and must not resolve to somebody."""
    resp = await client.get("/api/me", headers={"Authorization": "Bearer "})

    assert resp.status_code == 401


async def test_a_garbage_token_is_rejected(client, people, firebase_stub):
    resp = await client.get("/api/me", headers=bearer("tok-forged"))

    assert resp.status_code == 401


async def test_signed_in_but_never_registered_is_told_what_to_do(
    client, people, firebase_stub,
):
    """A real Firebase account with no ``users`` row. That is a state the app
    can actually reach — Firebase sign-up succeeded, ``POST /api/register``
    failed — and the fix is a call it can make, so say so rather than 401."""
    resp = await client.get("/api/me", headers=bearer("tok-unregistered"))

    assert resp.status_code == 403
    assert "/api/register" in resp.json()["detail"]
