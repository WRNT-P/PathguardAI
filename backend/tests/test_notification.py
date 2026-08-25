"""Phase 4 — the push, and the cooldown that keeps it from becoming noise.

The chain these tests close: an ``alerts`` row now leaves the server. The part
worth testing hardest is not the send but the *rate*. ``risk.py`` writes an alert
every scoring round its condition holds, and GPS ingest scores once a minute, so
a patient who sits in a danger zone generates an alert a minute indefinitely. A
caregiver pushed once a minute turns notifications off, and after that the system
is worse than having none — everyone believes they are covered and nobody is.

So: alerts stay per-round (they are the audit trail), and the cooldown lives at
the send boundary in notification.py, keyed per (patient, alert_type) on the
``push_notifications`` table.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from firebase_admin import messaging
from sqlalchemy import func, select

from app.db import crud
from app.db.models import DeviceToken, PushNotification
from app.services import notification
from app.services.notification import notify_alert

pytestmark = pytest.mark.asyncio

COOLDOWN_S = 600.0


class FakeFCM:
    """Stands in for ``messaging.send``; records every message it is handed."""

    def __init__(self, raises: Exception | None = None):
        self.sent: list[messaging.Message] = []
        self.raises = raises

    def __call__(self, message):
        self.sent.append(message)
        if self.raises is not None:
            raise self.raises
        return "projects/test/messages/1"

    @property
    def tokens(self) -> list[str]:
        return [m.token for m in self.sent]


@pytest.fixture
def fcm(monkeypatch):
    fake = FakeFCM()
    monkeypatch.setattr(notification.messaging, "send", fake)
    return fake


async def _pair(db, *, with_device: bool = True) -> tuple[int, int]:
    """A caregiver and a patient assigned to them. Returns (patient_id, caregiver_id)."""
    caregiver = await crud.create_user(
        db, firebase_uid="cg-uid", name="Caregiver", role="caregiver"
    )
    patient = await crud.create_user(
        db, firebase_uid="pt-uid", name="Patient", role="patient",
        caregiver_id=caregiver.id,
    )
    if with_device:
        await crud.upsert_device_token(db, caregiver.id, "tok-phone-1", "android")
    await db.commit()
    return patient.id, caregiver.id


async def _alert(db, patient_id: int, alert_type: str = "geofence"):
    alert = await crud.save_alert(
        db, patient_id, alert_type=alert_type, severity="critical",
        message="Patient entered a danger zone — risk 91%.",
        latitude=13.77, longitude=100.555,
    )
    await db.commit()
    return alert


async def test_alert_reaches_the_caregivers_device(db_session, fcm):
    patient_id, _ = await _pair(db_session)
    alert = await _alert(db_session, patient_id)

    result = await notify_alert(db_session, alert, COOLDOWN_S)

    assert result == {"status": "sent", "recipients": 1}
    assert fcm.tokens == ["tok-phone-1"]


async def test_payload_carries_the_coordinates_the_app_needs_to_open_the_map(
    db_session, fcm
):
    patient_id, _ = await _pair(db_session)
    alert = await _alert(db_session, patient_id)

    await notify_alert(db_session, alert, COOLDOWN_S)

    data = fcm.sent[0].data
    # FCM data values must be strings — a float here is a silent send failure.
    assert all(isinstance(v, str) for v in data.values())
    assert data["patient_id"] == str(patient_id)
    assert data["alert_type"] == "geofence"
    assert float(data["latitude"]) == pytest.approx(13.77)
    assert float(data["longitude"]) == pytest.approx(100.555)


async def test_second_alert_of_the_same_type_is_suppressed_inside_the_cooldown(
    db_session, fcm
):
    """The headline case: a patient who stays put keeps generating alerts."""
    patient_id, _ = await _pair(db_session)

    first = await notify_alert(db_session, await _alert(db_session, patient_id),
                               COOLDOWN_S)
    second = await notify_alert(db_session, await _alert(db_session, patient_id),
                                COOLDOWN_S)

    assert first["status"] == "sent"
    assert second["status"] == "cooldown"
    assert len(fcm.sent) == 1


async def test_a_different_alert_type_is_never_suppressed_by_another(db_session, fcm):
    """GPS going dark must not be swallowed because a geofence push just went out."""
    patient_id, _ = await _pair(db_session)

    await notify_alert(db_session, await _alert(db_session, patient_id, "geofence"),
                       COOLDOWN_S)
    other = await notify_alert(
        db_session, await _alert(db_session, patient_id, "gps_loss"), COOLDOWN_S
    )

    assert other["status"] == "sent"
    assert len(fcm.sent) == 2


async def test_the_cooldown_expires(db_session, fcm):
    patient_id, _ = await _pair(db_session)
    alert = await _alert(db_session, patient_id)

    db_session.add(PushNotification(
        patient_id=patient_id, alert_id=alert.id, alert_type="geofence",
        recipients=1,
        sent_at=datetime.now(timezone.utc) - timedelta(seconds=COOLDOWN_S + 60),
    ))
    await db_session.commit()

    assert (await notify_alert(db_session, alert, COOLDOWN_S))["status"] == "sent"


async def test_a_patient_with_no_caregiver_is_logged_not_crashed(db_session, fcm):
    orphan = await crud.create_user(
        db_session, firebase_uid="lonely", name="No caregiver", role="patient"
    )
    await db_session.commit()
    alert = await _alert(db_session, orphan.id)

    assert (await notify_alert(db_session, alert, COOLDOWN_S))["status"] == "no_caregiver"
    assert fcm.sent == []


async def test_a_caregiver_who_never_opened_the_app_is_not_a_crash(db_session, fcm):
    patient_id, _ = await _pair(db_session, with_device=False)
    alert = await _alert(db_session, patient_id)

    assert (await notify_alert(db_session, alert, COOLDOWN_S))["status"] == "no_caregiver"


async def test_a_failed_send_does_not_start_the_cooldown(db_session, monkeypatch):
    """A push that never left must not lock out the retry sixty seconds later."""
    patient_id, _ = await _pair(db_session)
    monkeypatch.setattr(notification.messaging, "send",
                        FakeFCM(raises=RuntimeError("FCM unreachable")))
    alert = await _alert(db_session, patient_id)

    result = await notify_alert(db_session, alert, COOLDOWN_S)

    assert result["status"] == "failed"
    assert await db_session.scalar(
        select(func.count()).select_from(PushNotification)) == 0


async def test_an_unregistered_token_is_dropped(db_session, monkeypatch):
    """Firebase rejects tokens from uninstalled apps forever; keep one and every
    later push fails on it."""
    patient_id, _ = await _pair(db_session)
    monkeypatch.setattr(
        notification.messaging, "send",
        FakeFCM(raises=messaging.UnregisteredError("token no longer valid")),
    )
    alert = await _alert(db_session, patient_id)

    result = await notify_alert(db_session, alert, COOLDOWN_S)
    await db_session.commit()

    assert result["status"] == "failed"
    assert await db_session.scalar(
        select(func.count()).select_from(DeviceToken)) == 0


# ── End to end through the API ───────────────────────────────────────────────

async def test_a_patient_parked_in_a_danger_zone_is_pushed_once_not_per_round(
    client, db_session, fcm
):
    """The production failure mode, driven through the real endpoints.

    A patient standing inside a seeded danger zone raises an alert on *every*
    scoring round — correct, and what the caregiver's alert history should show.
    They must still only be pushed once per cooldown window.
    """
    from app.db.models import Alert

    cg = await client.post("/api/register", json={
        "firebase_uid": "e2e-cg", "name": "Caregiver", "role": "caregiver"})
    caregiver_id = cg.json()["id"]
    pt = await client.post("/api/register", json={
        "firebase_uid": "e2e-pt", "name": "Patient", "role": "patient",
        "caregiver_id": caregiver_id})
    patient_id = pt.json()["id"]

    await client.post("/api/devices/token", json={
        "user_id": caregiver_id, "token": "tok-e2e-000001", "platform": "android"})

    # Seeded danger zone: highway interchange at 13.77, 100.555, r = 150 m.
    now = datetime.now(timezone.utc)
    points = [{
        "patient_id": patient_id,
        "latitude": 13.7700,
        "longitude": 100.5550,
        "speed": 0.2,
        "recorded_at": (now - timedelta(seconds=30 * (30 - i))).isoformat(),
    } for i in range(30)]
    resp = await client.post("/api/gps/batch", json={"points": points})
    assert resp.status_code in (200, 201)

    # Two more scoring rounds, bypassing the 60 s ingest throttle the way a
    # caregiver refreshing the dashboard would.
    for _ in range(2):
        r = await client.get(f"/api/risk/{patient_id}")
        assert r.status_code == 200
        assert r.json()["emergency"] is True

    # Count by type. The rounds also raise gps_loss alerts, but only under
    # SQLite: recorded_at comes back naive there, so detect_gps_gap cannot
    # compare it to an aware now() and takes its safety-biased branch. Checked
    # against Neon on 2026-08-22 — timestamptz returns aware, the gap measures
    # numerically, and no spurious gps_loss appears. Their presence here is
    # useful anyway: it shows the two types do not suppress each other.
    alerts = await db_session.scalar(
        select(func.count()).select_from(Alert)
        .where(Alert.patient_id == patient_id, Alert.alert_type == "geofence"))
    pushes = await db_session.scalar(
        select(func.count()).select_from(PushNotification)
        .where(PushNotification.patient_id == patient_id,
               PushNotification.alert_type == "geofence"))

    assert alerts >= 2, "the alert history should record every round"
    assert pushes == 1, "but the caregiver is pushed once per cooldown window"
    assert [m.data["alert_type"] for m in fcm.sent].count("geofence") == 1
