"""Phase 7 — who is calling, and whose patient they may ask about.

Until this existed, every endpoint was open and patient ids were sequential
integers, so anyone holding the tunnel URL could read a dementia patient's live
position by guessing "1". That was a stated, accepted risk while the alert chain
was being built (plan D5). These tests are the proof it is closed.

Firebase itself is never called here: ``verify_firebase_token`` is replaced with
a fixed token-to-uid map. What is under test is everything downstream of a valid
signature — mapping a uid to an internal user, and deciding whose data that user
may touch. Signature verification is Firebase's job and testing it would only
test the mock.

Both modes matter. ``AUTH_ENABLED`` defaults to off, which is why the other 207
tests in this suite send no tokens; the last test here pins that open state down
deliberately so nobody reads its absence as an oversight.
"""
from __future__ import annotations

import pytest

from app.db import crud
from app.services import auth

pytestmark = pytest.mark.asyncio

PATIENT_UID = "uid-patient"
CAREGIVER_UID = "uid-caregiver"
STRANGER_UID = "uid-stranger"
UNREGISTERED_UID = "uid-never-registered"

_TOKENS = {
    "tok-patient": PATIENT_UID,
    "tok-caregiver": CAREGIVER_UID,
    "tok-stranger": STRANGER_UID,
    "tok-unregistered": UNREGISTERED_UID,
}


@pytest.fixture
def auth_on(monkeypatch):
    """Turn the switch on and stub out Firebase's signature check."""
    def _verify(token: str) -> str:
        if token not in _TOKENS:
            raise auth._unauthorized("invalid or expired ID token")
        return _TOKENS[token]

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "verify_firebase_token", _verify)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def people(db_session):
    """A caregiver, their patient, and an unrelated caregiver."""
    caregiver = await crud.create_user(
        db_session, firebase_uid=CAREGIVER_UID, name="Caregiver", role="caregiver")
    stranger = await crud.create_user(
        db_session, firebase_uid=STRANGER_UID, name="Stranger", role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid=PATIENT_UID, name="Patient", role="patient",
        caregiver_id=caregiver.id)
    await db_session.commit()
    return {"patient": patient.id, "caregiver": caregiver.id,
            "stranger": stranger.id}


# ── Authentication ───────────────────────────────────────────────────────────

async def test_a_request_with_no_token_is_rejected(client, people, auth_on):
    resp = await client.get(f"/api/patients/{people['patient']}/alerts")

    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_a_token_firebase_will_not_vouch_for_is_rejected(
    client, people, auth_on
):
    resp = await client.get(f"/api/patients/{people['patient']}/alerts",
                            headers=bearer("tok-forged"))

    assert resp.status_code == 401


async def test_a_signed_in_account_with_no_users_row_is_told_to_register(
    client, people, auth_on
):
    """A real Firebase account that skipped /api/register is a real state, and
    the app can fix it — so it must not look like a rejection."""
    resp = await client.get(f"/api/patients/{people['patient']}/alerts",
                            headers=bearer("tok-unregistered"))

    assert resp.status_code == 403
    assert "register" in resp.json()["detail"]


# ── Authorization ────────────────────────────────────────────────────────────

async def test_a_patient_may_read_their_own_data(client, people, auth_on):
    resp = await client.get(f"/api/patients/{people['patient']}/alerts",
                            headers=bearer("tok-patient"))

    assert resp.status_code == 200


async def test_a_caregiver_may_read_their_own_patient(client, people, auth_on):
    resp = await client.get(f"/api/patients/{people['patient']}/track",
                            headers=bearer("tok-caregiver"))

    assert resp.status_code == 200


async def test_another_caregiver_may_not(client, people, auth_on):
    """The headline: guessing a patient id is no longer enough."""
    resp = await client.get(f"/api/patients/{people['patient']}/track",
                            headers=bearer("tok-stranger"))

    assert resp.status_code == 403
    assert "not your patient" in resp.json()["detail"]


def _patient_scoped_routes() -> list[tuple[str, str]]:
    """Every mounted route carrying a ``{patient_id}``, read out of the app.

    This used to be a hand-written list whose docstring claimed it walked all of
    them. It did not: `PUT .../places/home` and `GET .../trip-requests` were both
    added after it and neither was noticed, because nothing connected the list to
    the router. A list that has to be maintained by hand is the same hole it was
    written to close.
    """
    from app.main import app

    def walk(routes):
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from walk(included.routes)
                continue
            nested = getattr(route, "routes", None)
            if nested:
                yield from walk(nested)
                continue
            if hasattr(route, "path"):
                yield route

    found = []
    for route in walk(app.routes):
        if "{patient_id}" not in route.path:
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            found.append((method, route.path))
    return sorted(found)


@pytest.mark.parametrize("method,path", _patient_scoped_routes())
async def test_every_patient_scoped_route_is_guarded(
    client, people, auth_on, method, path
):
    """One missed route is the whole hole, so this walks all of them — for real.

    Writes are included, not just reads: `PUT .../places/home` can overwrite a
    patient's home, and `POST .../places` can replace their whole pin set, which
    would take scoring down to 56-medium everywhere.
    """
    url = path.format(patient_id=people["patient"])
    resp = await client.request(method, url, headers=bearer("tok-stranger"),
                                json={})

    # 403 before the body is looked at. A 422 here would mean the guard runs
    # after validation, and a stranger could probe which payloads are valid.
    assert resp.status_code == 403, f"{method} {path} is not guarded"


async def test_gps_cannot_be_written_in_someone_elses_name(client, people, auth_on):
    """patient_id rides in the body here, so the check cannot come from the path."""
    resp = await client.post("/api/gps", headers=bearer("tok-stranger"), json={
        "patient_id": people["patient"],
        "latitude": 13.7563, "longitude": 100.5018,
        "recorded_at": "2026-08-22T10:00:00Z",
    })

    assert resp.status_code == 403


async def test_sos_cannot_be_raised_in_someone_elses_name(client, people, auth_on):
    """Same shape as GPS — patient_id is in the body — but a worse hole.

    An SOS skips risk scoring entirely and lands as ``critical``, so a stranger
    who could raise one would put an alert on another family's phone that the
    system has no way to contradict.
    """
    resp = await client.post("/api/sos", headers=bearer("tok-stranger"),
                             json={"patient_id": people["patient"]})

    assert resp.status_code == 403


async def test_pins_cannot_be_written_for_someone_elses_patient(
    client, people, auth_on
):
    resp = await client.post(
        f"/api/patients/{people['patient']}/places",
        headers=bearer("tok-stranger"),
        json={"places": [{
            "place_name": "not theirs", "latitude": 13.75, "longitude": 100.50,
            "visit_rank": "daily_live", "stay_rank": "all_day",
        }]},
    )

    assert resp.status_code == 403


async def test_a_device_token_may_only_be_registered_for_your_own_account(
    client, people, auth_on
):
    """Otherwise you point another family's alerts at your own phone."""
    resp = await client.post("/api/devices/token", headers=bearer("tok-stranger"),
                             json={"user_id": people["caregiver"],
                                   "token": "tok-fcm-123456", "platform": "android"})

    assert resp.status_code == 403


async def test_you_cannot_register_under_someone_elses_firebase_uid(
    client, auth_on
):
    """That would hand you their patient's data for the life of the account."""
    resp = await client.post("/api/register", headers=bearer("tok-stranger"),
                             json={"firebase_uid": "uid-somebody-else",
                                   "name": "Impostor", "role": "caregiver"})

    assert resp.status_code == 403


async def test_someone_elses_alert_cannot_be_marked_handled(
    client, db_session, people, auth_on
):
    alert = await crud.save_alert(
        db_session, people["patient"], alert_type="geofence", severity="critical",
        message="danger zone", latitude=13.77, longitude=100.555)
    await db_session.commit()

    resp = await client.patch(f"/api/alerts/{alert.id}",
                              headers=bearer("tok-stranger"),
                              json={"resolved": True})

    assert resp.status_code == 403
    await db_session.refresh(alert)
    assert alert.resolved is False, "and nothing was written before the check"


async def test_the_rule_knowledge_base_needs_a_signed_in_caller(client, auth_on):
    """Not one patient's data — the settings every patient is scored against."""
    assert (await client.get("/api/admin/rules")).status_code == 401
    assert (await client.get("/api/danger-zones")).status_code == 401


# ── The default ──────────────────────────────────────────────────────────────

async def test_with_the_switch_off_everything_is_open(client, people):
    """Pinned on purpose. This is the shipped default (plan D5) and the reason
    the rest of the suite sends no tokens: the pilot runs watched, on a laptop,
    behind a URL nobody shares. It is also exactly what an attacker gets if that
    URL leaks, which is why AUTH_ENABLED exists and why flipping it is a
    decision someone has to make, not a detail."""
    assert auth.AUTH_ENABLED is False

    resp = await client.get(f"/api/patients/{people['patient']}/track")

    assert resp.status_code == 200
