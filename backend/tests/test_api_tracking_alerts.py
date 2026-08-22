"""What the caregiver app calls after a push lands.

Phase 4 ends with a notification carrying a ``patient_id`` and a pair of
coordinates. Until these two routers existed there was nothing to call next:
the app could be told a patient had wandered and then had no way to show where,
or to show what had happened before. The reads themselves had been running for
days under ``scripts/demo_server.py`` against the real tables; this is the same
code, mounted where a phone can reach it.

``resolved`` is the other half. The column has existed since the table was
created and nothing ever wrote to it, so "I found her" had no representation
anywhere in the system.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import crud
from app.db.models import Alert

pytestmark = pytest.mark.asyncio

BANGKOK = (13.7563, 100.5018)


async def _patient(client, uid: str = "track-pt") -> int:
    resp = await client.post(
        "/api/register", json={"firebase_uid": uid, "name": "P", "role": "patient"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _points(db, patient_id: int, n: int, *, minutes_ago: int) -> None:
    """n readings ending ``minutes_ago`` minutes back, one minute apart."""
    now = datetime.now(timezone.utc)
    for i in range(n):
        await crud.save_gps_point(
            db, patient_id,
            latitude=BANGKOK[0] + i * 0.0001, longitude=BANGKOK[1],
            speed=1.0,
            recorded_at=now - timedelta(minutes=minutes_ago + (n - i)),
        )
    await db.commit()


# ── Track ────────────────────────────────────────────────────────────────────

async def test_the_track_comes_back_oldest_first_for_drawing(client, db_session):
    patient_id = await _patient(client)
    await _points(db_session, patient_id, 5, minutes_ago=1)

    body = (await client.get(f"/api/patients/{patient_id}/track")).json()

    assert body["count"] == 5
    times = [p["recorded_at"] for p in body["points"]]
    assert times == sorted(times), "a polyline drawn out of order is a scribble"


async def test_track_fields_match_the_names_the_app_already_sends(client, db_session):
    """Same vocabulary as POST /api/gps — a client that can write can read."""
    patient_id = await _patient(client)
    await _points(db_session, patient_id, 3, minutes_ago=1)

    point = (await client.get(f"/api/patients/{patient_id}/track")).json()["points"][0]

    assert set(point) == {"latitude", "longitude", "recorded_at", "speed",
                          "synthetic_injected"}
    assert point["latitude"] == pytest.approx(BANGKOK[0], abs=1e-3)


async def test_an_old_track_is_shown_rather_than_an_empty_map(client, db_session):
    """A phone off since this morning must not render as "no data" — that reads
    as a working system with a stationary patient."""
    patient_id = await _patient(client)
    await _points(db_session, patient_id, 4, minutes_ago=60 * 20)  # 20 h ago

    body = (await client.get(f"/api/patients/{patient_id}/track?hours=6")).json()

    assert body["count"] == 4


async def test_an_unknown_patient_is_a_404_not_an_empty_track(client):
    resp = await client.get("/api/patients/999999/track")

    assert resp.status_code == 404
    assert "register" in resp.json()["detail"]


# ── Alerts ───────────────────────────────────────────────────────────────────

async def _alert(db, patient_id: int, alert_type: str, minutes_ago: int) -> Alert:
    alert = await crud.save_alert(
        db, patient_id, alert_type=alert_type, severity="high",
        message=f"{alert_type} at {minutes_ago} min ago",
        latitude=BANGKOK[0], longitude=BANGKOK[1],
    )
    alert.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    await db.commit()
    return alert


async def test_alerts_come_back_newest_first(client, db_session):
    patient_id = await _patient(client)
    await _alert(db_session, patient_id, "gps_loss", minutes_ago=90)
    await _alert(db_session, patient_id, "geofence", minutes_ago=5)

    body = (await client.get(f"/api/patients/{patient_id}/alerts")).json()

    assert body["count"] == 2
    assert body["alerts"][0]["alert_type"] == "geofence"


async def test_the_feed_can_be_limited(client, db_session):
    patient_id = await _patient(client)
    for i in range(5):
        await _alert(db_session, patient_id, "geofence", minutes_ago=i + 1)

    body = (await client.get(f"/api/patients/{patient_id}/alerts?limit=2")).json()

    assert body["count"] == 2


async def test_resolving_an_alert_records_when(client, db_session):
    patient_id = await _patient(client)
    alert = await _alert(db_session, patient_id, "geofence", minutes_ago=2)

    resp = await client.patch(f"/api/alerts/{alert.id}", json={"resolved": True})

    assert resp.status_code == 200
    assert resp.json()["resolved"] is True
    await db_session.refresh(alert)
    assert alert.resolved_at is not None


async def test_resolving_is_reversible(client, db_session):
    """Tapped on the wrong row in a hurry — that has to be undoable."""
    patient_id = await _patient(client)
    alert = await _alert(db_session, patient_id, "geofence", minutes_ago=2)

    await client.patch(f"/api/alerts/{alert.id}", json={"resolved": True})
    resp = await client.patch(f"/api/alerts/{alert.id}", json={"resolved": False})

    assert resp.json()["resolved"] is False
    await db_session.refresh(alert)
    assert alert.resolved_at is None


async def test_patching_an_alert_that_does_not_exist_is_a_404(client):
    resp = await client.patch("/api/alerts/999999", json={"resolved": True})

    assert resp.status_code == 404


async def test_alerts_for_an_unknown_patient_are_a_404(client):
    resp = await client.get("/api/patients/999999/alerts")

    assert resp.status_code == 404
