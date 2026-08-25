"""POST /api/sos — the alert the system does not have to infer.

Every other alert in this backend is a guess that crossed a threshold. This one
is a person pressing a button, and the tests below are mostly about the ways the
*rest* of the system could quietly swallow that press:

* the 60 s risk-scoring throttle, if SOS had been routed through POST /api/gps;
* ``decide_emergency``, which would score a patient standing at home as ``low``
  and raise nothing;
* the push cooldown, which is keyed on (patient, alert_type) and would let an
  automatic ``emergency`` from three minutes ago suppress the patient's press.

The last one is the reason ``alert_type`` is ``"sos"`` and not ``"emergency"``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from firebase_admin import messaging
from sqlalchemy import select

from app.db import crud, rule_repository
from app.db.models import Alert
from app.services import notification

pytestmark = pytest.mark.asyncio

HOME_LAT, HOME_LNG = 13.7563, 100.5018


class FakeFCM:
    """Stands in for ``messaging.send``; records every message it is handed."""

    def __init__(self):
        self.sent: list[messaging.Message] = []

    def __call__(self, message):
        self.sent.append(message)
        return "projects/test/messages/1"


@pytest.fixture
def fcm(monkeypatch):
    fake = FakeFCM()
    monkeypatch.setattr(notification.messaging, "send", fake)
    return fake


async def _pair(db, *, with_device: bool = True) -> int:
    """A caregiver with a registered phone, and their patient. Returns patient_id."""
    caregiver = await crud.create_user(
        db, firebase_uid="sos-cg", name="Caregiver", role="caregiver"
    )
    patient = await crud.create_user(
        db, firebase_uid="sos-pt", name="Patient", role="patient",
        caregiver_id=caregiver.id,
    )
    if with_device:
        await crud.upsert_device_token(db, caregiver.id, "tok-sos-1", "android")
    await db.commit()
    return patient.id


async def _alert_rows(db, patient_id: int) -> list[Alert]:
    return list((await db.execute(
        select(Alert).where(Alert.patient_id == patient_id).order_by(Alert.id)
    )).scalars())


async def test_pressing_the_button_reaches_the_caregivers_phone(client, db_session, fcm):
    """The whole point, end to end: press -> alert row -> FCM message."""
    patient_id = await _pair(db_session)

    resp = await client.post(
        "/api/sos",
        json={"patient_id": patient_id, "latitude": HOME_LAT, "longitude": HOME_LNG},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["push"] == "sent"
    assert body["severity"] == "critical"
    assert body["latitude"] == pytest.approx(HOME_LAT)

    rows = await _alert_rows(db_session, patient_id)
    assert [r.alert_type for r in rows] == ["sos"]
    assert len(fcm.sent) == 1
    assert fcm.sent[0].data["alert_type"] == "sos"


async def test_sos_fires_where_risk_scoring_would_stay_silent(client, db_session, fcm):
    """Why this is not a flag on POST /api/gps.

    A patient pressing the button in their own living room scores ``low`` — the
    scorer is right and the button still has to work. Nothing here consults a
    score at all: no GPS history exists in this test, so the risk pipeline would
    have returned ``no_data`` and raised nothing.
    """
    patient_id = await _pair(db_session)
    assert await crud.get_latest_gps(db_session, patient_id) is None

    resp = await client.post(
        "/api/sos",
        json={"patient_id": patient_id, "latitude": HOME_LAT, "longitude": HOME_LNG},
    )

    assert resp.status_code == 201
    assert resp.json()["push"] == "sent"
    assert await crud.get_latest_risk_score(db_session, patient_id) is None


async def test_an_automatic_emergency_does_not_suppress_a_button_press(
    client, db_session, fcm
):
    """The cooldown bucket separation, stated as a test.

    The push cooldown is keyed on (patient_id, alert_type). If SOS reused
    ``"emergency"``, a wandering alert pushed seconds earlier would swallow the
    patient's press for the next ten minutes and nothing would report it.
    """
    patient_id = await _pair(db_session)

    auto = await crud.save_alert(
        db_session, patient_id, alert_type="emergency", severity="high",
        message="High risk (86%) — wandering.", latitude=HOME_LAT, longitude=HOME_LNG,
    )
    await db_session.commit()
    assert (await notification.notify_alert(db_session, auto, 600.0))["status"] == "sent"

    resp = await client.post("/api/sos", json={"patient_id": patient_id})

    assert resp.json()["push"] == "sent", (
        "an automatic emergency suppressed the patient's own SOS — check that "
        "alert_type is 'sos' and not 'emergency'"
    )
    assert len(fcm.sent) == 2


async def test_a_burst_of_presses_is_collapsed_but_every_press_is_recorded(
    client, db_session, fcm
):
    """A patient with dementia is exactly the person likely to press repeatedly.

    Thirty pushes mutes the app, which is the failure the cooldown exists to
    prevent — but the alerts table is the audit trail and must keep all of them,
    so the caregiver's timeline and the dashboard still show every press.
    """
    patient_id = await _pair(db_session)

    statuses = []
    for _ in range(3):
        resp = await client.post("/api/sos", json={"patient_id": patient_id})
        assert resp.status_code == 201
        statuses.append(resp.json()["push"])

    assert statuses == ["sent", "cooldown", "cooldown"]
    assert len(fcm.sent) == 1

    rows = await _alert_rows(db_session, patient_id)
    assert len(rows) == 3, "the audit trail lost a press"
    assert all(r.alert_type == "sos" for r in rows)


async def test_the_cooldown_is_its_own_rule_kb_row_not_the_push_one(client, db_session):
    """Tunable at runtime like every other threshold, and separate on purpose.

    Sharing push_cooldown_seconds would mean a caregiver who slows down wandering
    notifications also slows down SOS, which is not a trade anyone chose.
    """
    sos_s = await rule_repository.get_threshold(
        db_session, rule_repository.SOS_COOLDOWN_SECONDS
    )
    push_s = await rule_repository.get_threshold(
        db_session, rule_repository.PUSH_COOLDOWN_SECONDS
    )
    assert sos_s == 60.0
    assert sos_s != push_s


async def test_missing_coordinates_fall_back_to_the_last_known_position(
    client, db_session, fcm
):
    """Pressed with the GPS chip cold, the alert still says where to look."""
    patient_id = await _pair(db_session)
    await crud.save_gps_point(
        db_session, patient_id=patient_id, latitude=HOME_LAT, longitude=HOME_LNG,
        recorded_at=datetime.now(timezone.utc),
    )
    await db_session.commit()

    resp = await client.post("/api/sos", json={"patient_id": patient_id})

    assert resp.status_code == 201
    assert resp.json()["latitude"] == pytest.approx(HOME_LAT)
    rows = await _alert_rows(db_session, patient_id)
    assert rows[0].latitude == pytest.approx(HOME_LAT)


async def test_no_position_anywhere_still_raises_the_alert(client, db_session, fcm):
    """An SOS with no coordinates is still an SOS.

    Refusing it because the phone could not get a fix would silence the button in
    exactly the conditions — indoors, underground, chip cold — where a patient is
    most likely to be lost and least likely to be tracked.
    """
    patient_id = await _pair(db_session)

    resp = await client.post("/api/sos", json={"patient_id": patient_id})

    assert resp.status_code == 201
    assert resp.json()["push"] == "sent"
    assert resp.json()["latitude"] is None
    assert len(await _alert_rows(db_session, patient_id)) == 1


async def test_unknown_patient_is_404_not_a_foreign_key_error(client):
    resp = await client.post("/api/sos", json={"patient_id": 999999})
    assert resp.status_code == 404
    assert "register" in resp.json()["detail"]


async def test_no_registered_caregiver_phone_still_stores_the_alert(
    client, db_session, fcm
):
    """FCM having nowhere to send is not a reason to lose the press.

    The dashboard reads ``alerts``; D6 makes it the caregiver's fallback channel.
    A 500 here would take away the fallback as well as the push.
    """
    patient_id = await _pair(db_session, with_device=False)

    resp = await client.post("/api/sos", json={"patient_id": patient_id})

    assert resp.status_code == 201
    assert resp.json()["push"] == "no_caregiver"
    assert len(await _alert_rows(db_session, patient_id)) == 1
    assert fcm.sent == []
