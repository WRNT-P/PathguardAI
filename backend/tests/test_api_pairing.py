"""L2-4 — the caregiver creates the patient, and a short code lets the phone in.

The point of these tests is not that a code round-trips. It is that turning on
``AUTH_ENABLED`` stops being the thing that kills ``POST /api/gps``. Before this
endpoint pair existed, a patient device had no Firebase account and no way to
get one — the app pairs by code — so flipping the flag would have 403'd every
GPS point the system exists to collect. ``test_paired_device_can_send_gps_with_auth_on``
is the one that proves that is closed; the rest guard the ways a pairing code
could quietly become a permanent credential.

Firebase is never called. ``_mint_custom_token`` is replaced with a stub that
records the uid it was asked to sign for, because what matters here is *which
account* the code hands over — signing is Firebase's job and mocking it would
only test the mock. The same reasoning as ``test_auth.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api import pairing
from app.db import crud
from app.db.models import PairingCode, User
from app.services import auth

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mint(monkeypatch):
    """Stand in for Firebase; remember every uid a token was minted for."""
    minted: list[str] = []

    def _fake(firebase_uid: str) -> str:
        minted.append(firebase_uid)
        return f"custom-token-for::{firebase_uid}"

    monkeypatch.setattr(pairing, "_mint_custom_token", _fake)
    return minted


@pytest.fixture
async def caregiver(db_session):
    row = await crud.create_user(
        db_session, firebase_uid="uid-caregiver", name="ผู้ดูแล", role="caregiver")
    await db_session.commit()
    return row.id


async def create_patient(client, caregiver_id, **over):
    body = {"name": "คุณยาย", "severity_level": 2, "caregiver_id": caregiver_id}
    body.update(over)
    return await client.post("/api/patients", json=body)


# ── creating the patient ─────────────────────────────────────────────────────

async def test_caregiver_creates_patient_and_gets_a_code(client, caregiver, db_session):
    r = await create_patient(client, caregiver)
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["caregiver_id"] == caregiver
    assert body["severity_level"] == 2
    assert body["name"] == "คุณยาย"

    # Displayed grouped, because a caregiver reads it aloud off one screen and
    # types it into another.
    assert body["pairing_code"][4] == "-"
    assert len(pairing.normalise(body["pairing_code"])) == pairing._CODE_LENGTH

    patient = await db_session.get(User, body["patient_id"])
    assert patient.role == "patient"
    assert patient.caregiver_id == caregiver
    assert patient.severity_level == 2
    # The whole design: the server chose the identity before the phone existed.
    assert patient.firebase_uid.startswith("pathguard:")


async def test_uid_is_random_not_derived_from_the_patient_id(client, caregiver, db_session):
    """The uid must not be a function of ``users.id``.

    ``patient_id`` is a sequential integer that appears in every URL the app
    calls, so a uid derived from it would let anyone who has seen one patient's
    id compute the identity of every other patient in the system.
    """
    uids = []
    for _ in range(5):
        r = await create_patient(client, caregiver)
        patient = await db_session.get(User, r.json()["patient_id"])
        uids.append(patient.firebase_uid)

    suffixes = [u.removeprefix("pathguard:") for u in uids]
    assert len(set(suffixes)) == len(suffixes)          # no repeats
    for suffix in suffixes:
        assert len(suffix) == 32                       # token_hex(16)
        assert all(c in "0123456789abcdef" for c in suffix)


async def test_severity_level_is_optional(client, caregiver, db_session):
    r = await create_patient(client, caregiver, severity_level=None)
    assert r.status_code == 201
    assert r.json()["severity_level"] is None


async def test_severity_level_outside_1_or_2_is_rejected(client, caregiver):
    r = await create_patient(client, caregiver, severity_level=3)
    assert r.status_code == 422


async def test_unknown_caregiver_is_404_not_a_dangling_row(client, db_session):
    r = await create_patient(client, 9999)
    assert r.status_code == 404
    assert (await db_session.execute(select(User))).scalars().all() == []


async def test_caregiver_id_required_while_auth_is_off(client, caregiver):
    """With no token there is nothing to infer the caregiver from."""
    r = await client.post("/api/patients", json={"name": "คุณยาย"})
    assert r.status_code == 422
    assert "caregiver_id" in r.json()["detail"]


async def test_two_patients_never_share_a_code(client, caregiver):
    first = (await create_patient(client, caregiver)).json()["pairing_code"]
    second = (await create_patient(client, caregiver)).json()["pairing_code"]
    assert first != second


# ── redeeming the code ───────────────────────────────────────────────────────

async def test_pairing_returns_a_token_for_that_patient(client, caregiver, mint, db_session):
    created = (await create_patient(client, caregiver)).json()
    patient = await db_session.get(User, created["patient_id"])

    r = await client.post("/api/pair", json={"code": created["pairing_code"]})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["patient_id"] == created["patient_id"]
    # The token must be for the patient's own account, not the caregiver's.
    assert mint == [patient.firebase_uid]
    assert patient.firebase_uid in body["firebase_custom_token"]


async def test_code_is_accepted_however_the_caregiver_types_it(client, caregiver, mint):
    created = (await create_patient(client, caregiver)).json()
    typed = created["pairing_code"].lower().replace("-", " ") + " "
    r = await client.post("/api/pair", json={"code": typed})
    assert r.status_code == 200


async def test_a_code_works_exactly_once(client, caregiver, mint):
    created = (await create_patient(client, caregiver)).json()
    code = created["pairing_code"]

    assert (await client.post("/api/pair", json={"code": code})).status_code == 200
    second = await client.post("/api/pair", json={"code": code})
    assert second.status_code == 404
    # Only one token was ever minted — a replayed code must not hand out another.
    assert len(mint) == 1


async def test_expired_code_is_refused(client, caregiver, mint, db_session):
    created = (await create_patient(client, caregiver)).json()
    row = (await db_session.execute(
        select(PairingCode).where(PairingCode.patient_id == created["patient_id"])
    )).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    r = await client.post("/api/pair", json={"code": created["pairing_code"]})
    assert r.status_code == 404
    assert mint == []


async def test_unknown_code_is_refused(client, mint):
    r = await client.post("/api/pair", json={"code": "ZZZZ-ZZZZ"})
    assert r.status_code == 404
    assert mint == []


async def test_every_rejection_reads_the_same(client, caregiver, mint, db_session):
    """A different message for "expired" would confirm the code exists."""
    created = (await create_patient(client, caregiver)).json()
    used = await client.post("/api/pair", json={"code": created["pairing_code"]})
    assert used.status_code == 200

    replayed = await client.post("/api/pair", json={"code": created["pairing_code"]})
    unknown = await client.post("/api/pair", json={"code": "ZZZZ-ZZZZ"})
    assert replayed.json()["detail"] == unknown.json()["detail"]


async def test_code_survives_a_failed_mint(client, caregiver, monkeypatch):
    """Firebase being unreachable must not burn the caregiver's only code."""
    created = (await create_patient(client, caregiver)).json()

    def _boom(firebase_uid: str) -> str:
        raise RuntimeError("firebase unreachable")

    monkeypatch.setattr(pairing, "_mint_custom_token", _boom)
    with pytest.raises(RuntimeError):
        await client.post("/api/pair", json={"code": created["pairing_code"]})

    monkeypatch.setattr(pairing, "_mint_custom_token", lambda uid: "tok")
    retried = await client.post("/api/pair", json={"code": created["pairing_code"]})
    assert retried.status_code == 200


# ── the reason this endpoint pair exists ─────────────────────────────────────

async def test_paired_device_can_send_gps_with_auth_on(client, caregiver, mint,
                                                       db_session, monkeypatch):
    """The whole point of L2-4, end to end.

    Pair the device, then turn ``AUTH_ENABLED`` on and have that device post a
    GPS point the way the phone will. Before this existed the same flip took
    ``/api/gps`` down for the patient, because the device had no Firebase
    identity to present.
    """
    created = (await create_patient(client, caregiver)).json()
    paired = await client.post("/api/pair", json={"code": created["pairing_code"]})
    assert paired.status_code == 200

    patient = await db_session.get(User, created["patient_id"])
    device_uid = patient.firebase_uid

    # signInWithCustomToken() would now yield an ID token carrying this uid.
    def _verify(token: str) -> str:
        if token == "id-token-of-paired-device":
            return device_uid
        if token == "id-token-of-some-other-phone":
            return "uid-not-registered"
        raise auth._unauthorized("invalid or expired ID token")

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "verify_firebase_token", _verify)

    point = {
        "patient_id": created["patient_id"],
        "latitude": 13.7563,
        "longitude": 100.5018,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    ok = await client.post("/api/gps", json=point,
                           headers={"Authorization": "Bearer id-token-of-paired-device"})
    assert ok.status_code in (200, 201), ok.text

    # A phone that never redeemed a code has no row behind its uid.
    stranger = await client.post(
        "/api/gps", json=point,
        headers={"Authorization": "Bearer id-token-of-some-other-phone"})
    assert stranger.status_code == 403

    unauthenticated = await client.post("/api/gps", json=point)
    assert unauthenticated.status_code == 401
