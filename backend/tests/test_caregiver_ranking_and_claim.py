"""Ranked caregivers (A4) and "I'll go and get them" (A5), 2026-08-28.

The report's line is "alert every caregiver, ranked by distance, tap to claim".
The fan-out shipped with the schema; these are the other two thirds.

The design decision most of this file exists to hold down: **a caregiver whose
position is stale, or missing entirely, is demoted — never removed.** Filtering
by freshness is the obvious implementation and it fails in the worst possible
place. The app side has not yet said how often it reports a position, so any
cut-off is a guess, and a guess that is tighter than reality turns this endpoint
into "nobody is available" at the moment a patient is missing. A list in the
wrong order is something a human standing there can still read.

The claim's rule is the opposite one, and equally deliberate: exactly one person
can hold an alert, and a second caregiver is told *who* holds it. Two people
driving to the same place while a third assumes it is handled is the accident
this feature exists to prevent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import crud
from app.db.models import Alert
from app.services import auth
from app.services import notification
from tests.test_auth import auth_on, bearer  # noqa: F401  (fixture import)

pytestmark = pytest.mark.asyncio

HOME = (13.7563, 100.5018)
METRES_PER_DEG_LAT = 2 * 3.141592653589793 * 6_371_000.0 / 360.0

PRIMARY_UID = "uid-rank-primary"
SECOND_UID = "uid-rank-second"


def north(metres: float) -> tuple[float, float]:
    return (HOME[0] + metres / METRES_PER_DEG_LAT, HOME[1])


@pytest.fixture
async def household(db_session):
    """A patient at HOME, two caregivers, and an unrelated one."""
    primary = await crud.create_user(
        db_session, firebase_uid=PRIMARY_UID, name="ลูกสาว", role="caregiver")
    second = await crud.create_user(
        db_session, firebase_uid=SECOND_UID, name="ลูกชาย", role="caregiver")
    stranger = await crud.create_user(
        db_session, firebase_uid="uid-rank-stranger", name="คนอื่น",
        role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid="uid-rank-patient", name="คุณยาย",
        role="patient", caregiver_id=primary.id)
    await db_session.flush()
    await crud.link_caregiver(db_session, patient.id, second.id)
    await crud.save_gps_point(
        db_session, patient_id=patient.id, latitude=HOME[0], longitude=HOME[1],
        recorded_at=datetime.now(timezone.utc))
    await db_session.commit()
    return {"patient": patient.id, "primary": primary.id,
            "second": second.id, "stranger": stranger.id}


async def put_location(db_session, user_id, metres, age_s=0.0):
    """Place a caregiver `metres` north of the patient, `age_s` ago."""
    lat, lng = north(metres)
    user = await crud.update_user_location(db_session, user_id, lat, lng)
    if age_s:
        user.location_updated_at = (
            datetime.now(timezone.utc) - timedelta(seconds=age_s))
    await db_session.commit()


# ── A4: the ranking ──────────────────────────────────────────────────────────

async def test_the_nearest_caregiver_comes_first(client, db_session, household):
    await put_location(db_session, household["primary"], 5_000)
    await put_location(db_session, household["second"], 300)

    body = (await client.get(
        f"/api/patients/{household['patient']}/caregivers")).json()

    order = [c["caregiver_id"] for c in body["caregivers"]]
    assert order == [household["second"], household["primary"]]
    assert body["caregivers"][0]["distance_m"] == pytest.approx(300, abs=5)
    assert all(c["usable"] for c in body["caregivers"])


async def test_a_stale_position_is_demoted_and_not_dropped(
        client, db_session, household):
    """The whole argument of this feature in one test.

    The stale caregiver is *closer*. They still lose the top spot, because a
    position from an hour ago is not evidence about where somebody is now — but
    they stay on the list, because the family may well know something the
    timestamp does not.
    """
    await put_location(db_session, household["primary"], 100, age_s=7_200)
    await put_location(db_session, household["second"], 4_000)

    body = (await client.get(
        f"/api/patients/{household['patient']}/caregivers")).json()

    assert [c["caregiver_id"] for c in body["caregivers"]] \
        == [household["second"], household["primary"]]
    stale = body["caregivers"][1]
    assert stale["usable"] is False
    # Demoted, but everything about them is still there to act on.
    assert stale["distance_m"] == pytest.approx(100, abs=5)
    assert stale["location_age_s"] > 7_000


async def test_a_caregiver_who_never_reported_is_last_but_present(
        client, db_session, household):
    await put_location(db_session, household["second"], 4_000)

    body = (await client.get(
        f"/api/patients/{household['patient']}/caregivers")).json()

    assert len(body["caregivers"]) == 2
    never = body["caregivers"][-1]
    assert never["caregiver_id"] == household["primary"]
    assert never["distance_m"] is None
    assert never["location_age_s"] is None
    assert never["usable"] is False


async def test_nobody_is_dropped_when_every_position_is_stale(
        client, db_session, household):
    """The failure this design is chosen to avoid: an empty list mid-emergency."""
    await put_location(db_session, household["primary"], 900, age_s=86_400)
    await put_location(db_session, household["second"], 200, age_s=86_400)

    body = (await client.get(
        f"/api/patients/{household['patient']}/caregivers")).json()

    assert len(body["caregivers"]) == 2
    assert not any(c["usable"] for c in body["caregivers"])
    # Still ordered by distance among themselves — the nearest guess beats the
    # far one when every one of them is a guess.
    assert body["caregivers"][0]["caregiver_id"] == household["second"]


async def test_an_exact_tie_goes_to_the_primary(client, db_session, household):
    """`is_primary` is not a permission. Breaking a tie is the one thing it does."""
    await put_location(db_session, household["primary"], 1_000)
    await put_location(db_session, household["second"], 1_000)

    body = (await client.get(
        f"/api/patients/{household['patient']}/caregivers")).json()

    assert body["caregivers"][0]["caregiver_id"] == household["primary"]
    assert body["caregivers"][0]["is_primary"] is True


async def test_no_gps_for_the_patient_still_lists_the_caregivers(
        client, db_session):
    """Ranking needs both ends. Missing the patient's end is not an error — it is
    the state a patient is in before their phone has ever reported."""
    caregiver = await crud.create_user(
        db_session, firebase_uid="uid-nogps-cg", name="ลูก", role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid="uid-nogps-pt", name="ยาย", role="patient",
        caregiver_id=caregiver.id)
    await db_session.commit()
    await put_location(db_session, caregiver.id, 500)

    body = (await client.get(f"/api/patients/{patient.id}/caregivers")).json()

    assert body["patient_latitude"] is None
    assert len(body["caregivers"]) == 1
    assert body["caregivers"][0]["distance_m"] is None
    assert body["caregivers"][0]["usable"] is False


async def test_the_ranking_is_behind_the_patient_access_guard(
        client, household, auth_on, monkeypatch):
    """It names the patient's family and says where they are. Same door as the
    rest of the patient's data."""
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: "uid-rank-stranger")

    resp = await client.get(
        f"/api/patients/{household['patient']}/caregivers", headers=bearer("tok"))

    assert resp.status_code == 403


# ── A5: the claim ────────────────────────────────────────────────────────────

async def make_alert(db_session, patient_id) -> int:
    alert = await crud.save_alert(
        db_session, patient_id=patient_id, alert_type="wandering",
        severity="high", message="ออกนอกพื้นที่", latitude=HOME[0],
        longitude=HOME[1])
    await db_session.commit()
    return alert.id


async def test_claiming_records_who_is_going(
        client, db_session, household, auth_on, monkeypatch):
    alert_id = await make_alert(db_session, household["patient"])
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)

    resp = await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    assert resp.status_code == 200, resp.text
    body = resp.json()["alert"]
    assert body["claimed_by"] == household["second"]
    assert body["claimed_by_name"] == "ลูกชาย"
    assert body["claimed_at"] is not None
    # Claimed is not resolved. Somebody is on their way; nobody is home yet.
    assert body["resolved"] is False


async def test_a_second_claimer_is_told_who_holds_it(
        client, db_session, household, auth_on, monkeypatch):
    """409 with a name. "Taken" alone is not something the loser can act on."""
    alert_id = await make_alert(db_session, household["patient"])
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)
    assert (await client.post(
        f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))).status_code == 200

    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: PRIMARY_UID)
    resp = await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["claimed_by"] == household["second"]
    assert detail["claimed_by_name"] == "ลูกชาย"


async def test_the_claimer_is_not_pushed_their_own_decision(
        client, db_session, household, auth_on, monkeypatch):
    """The shared piece A4 and A5 both needed: exclude one person from the fan-out."""
    sent_to: list[str] = []
    monkeypatch.setattr(
        notification, "_send_one",
        lambda token, title, body, data: sent_to.append(token))

    await crud.upsert_device_token(
        db_session, household["primary"], "token-primary", "android")
    await crud.upsert_device_token(
        db_session, household["second"], "token-second", "android")
    alert_id = await make_alert(db_session, household["patient"])
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)

    resp = await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    assert resp.json()["push"] == "sent"
    assert sent_to == ["token-primary"]


async def test_re_claiming_your_own_alert_does_not_push_again(
        client, db_session, household, auth_on, monkeypatch):
    """A duplicate tap is not news. Re-notifying on every tap is how a channel
    that has to be read gets muted."""
    sent_to: list[str] = []
    monkeypatch.setattr(
        notification, "_send_one",
        lambda token, title, body, data: sent_to.append(token))
    await crud.upsert_device_token(
        db_session, household["primary"], "token-primary", "android")
    alert_id = await make_alert(db_session, household["patient"])
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)

    first = await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))
    second = await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    assert first.json()["push"] == "sent"
    assert second.status_code == 200
    assert second.json()["push"] == "duplicate"
    assert sent_to == ["token-primary"]


async def test_a_lone_caregiver_claiming_is_not_an_error(
        client, db_session, auth_on, monkeypatch):
    caregiver = await crud.create_user(
        db_session, firebase_uid="uid-lone-cg", name="ลูกคนเดียว", role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid="uid-lone-pt", name="ยาย", role="patient",
        caregiver_id=caregiver.id)
    await db_session.commit()
    alert_id = await make_alert(db_session, patient.id)
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: "uid-lone-cg")

    resp = await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    assert resp.status_code == 200
    assert resp.json()["push"] == "no_other_caregiver"


async def test_the_holder_can_change_their_mind(
        client, db_session, household, auth_on, monkeypatch):
    """Without release, "I'm going" and then not going leaves an alert that reads
    as handled while nobody is on their way."""
    alert_id = await make_alert(db_session, household["patient"])
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)
    await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    resp = await client.delete(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    assert resp.status_code == 200
    assert resp.json()["claimed_by"] is None
    assert resp.json()["claimed_at"] is None
    # And it is claimable again by somebody else.
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: PRIMARY_UID)
    again = await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))
    assert again.status_code == 200
    assert again.json()["alert"]["claimed_by"] == household["primary"]


async def test_only_the_holder_may_release(
        client, db_session, household, auth_on, monkeypatch):
    alert_id = await make_alert(db_session, household["patient"])
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)
    await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: PRIMARY_UID)
    resp = await client.delete(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    assert resp.status_code == 403


async def test_releasing_an_unclaimed_alert_is_a_conflict(
        client, db_session, household, auth_on, monkeypatch):
    alert_id = await make_alert(db_session, household["patient"])
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)

    resp = await client.delete(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    assert resp.status_code == 409


async def test_claiming_needs_a_signed_in_caregiver(client, db_session, household):
    """With auth off there is no "who". Recording a claim by nobody would tell a
    family that somebody is going when nothing was decided."""
    alert_id = await make_alert(db_session, household["patient"])

    resp = await client.post(f"/api/alerts/{alert_id}/claim")

    assert resp.status_code == 401


async def test_a_claim_shows_up_in_the_alert_feed(
        client, db_session, household, auth_on, monkeypatch):
    """The feed is where the other caregivers look. A claim nobody can see in it
    is a claim only the pusher knows about."""
    alert_id = await make_alert(db_session, household["patient"])
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)
    await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    body = (await client.get(
        f"/api/patients/{household['patient']}/alerts",
        headers=bearer("tok"))).json()

    row = next(a for a in body["alerts"] if a["id"] == alert_id)
    assert row["claimed_by"] == household["second"]
    assert row["claimed_at"] is not None


async def test_claiming_an_unknown_alert_is_404(
        client, household, auth_on, monkeypatch):
    # household so the caller is a registered caregiver: an unknown *uid* is a
    # 403 before the alert is ever looked up, which would pass this test for the
    # wrong reason.
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: SECOND_UID)
    resp = await client.post("/api/alerts/999999/claim", headers=bearer("tok"))
    assert resp.status_code == 404


async def test_a_stranger_cannot_claim_someone_elses_alert(
        client, db_session, household, auth_on, monkeypatch):
    alert_id = await make_alert(db_session, household["patient"])
    monkeypatch.setattr(auth, "verify_firebase_token", lambda t: "uid-rank-stranger")

    resp = await client.post(f"/api/alerts/{alert_id}/claim", headers=bearer("tok"))

    assert resp.status_code == 403
    assert await db_session.get(Alert, alert_id) is not None
