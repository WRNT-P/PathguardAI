"""End-to-end test of the Module 3 /api/risk endpoint.

Exercises the full pipeline: fetch (DB) -> collect raw factors (Module 2
detectors, sklearn — no TensorFlow) -> normalize -> score -> decide -> persist
RiskScore/Alert -> respond. Runs against the in-memory SQLite test DB.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.module3_risk.risk_data_collection import DANGER_ZONES
from app.db import crud

pytestmark = pytest.mark.asyncio

# Two known places, both clear of the hardcoded DANGER_ZONES.
PLACES = [
    {"cluster_id": 0, "latitude": 13.7460, "longitude": 100.5340,
     "visit_frequency": 40, "avg_stay_time": 120.0},
    {"cluster_id": 1, "latitude": 13.7510, "longitude": 100.5400,
     "visit_frequency": 12, "avg_stay_time": 30.0},
]


async def _seed_patient_with_history(db):
    """Create a patient, a behavioral profile, and ~10 days of GPS history."""
    user = await crud.create_user(db, firebase_uid="risk-uid", name="Pat", role="patient")
    await db.flush()
    await crud.upsert_behavioral_profile(
        db, user.id, known_places=json.dumps(PLACES), typical_range_km=2.0
    )

    now = datetime.now(timezone.utc)
    # Recent points near PLACES[1], moving (speed 1.2), one minute apart.
    for k in range(16):
        place = PLACES[k % 2]
        await crud.save_gps_point(
            db, user.id,
            latitude=place["latitude"], longitude=place["longitude"],
            speed=1.2, recorded_at=now - timedelta(minutes=16 - k),
        )
    await db.commit()
    return user.id


async def test_risk_no_data_returns_graceful_status(client):
    # No profile/GPS for this patient id -> no_data, not a 404.
    resp = await client.get("/api/risk/999999")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_data"
    assert body["risk_score"] is None


async def test_risk_ok_path_scores_and_persists(client, db_session):
    patient_id = await _seed_patient_with_history(db_session)

    resp = await client.get(f"/api/risk/{patient_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "ok"
    assert body["patient_id"] == patient_id
    assert 0.0 <= body["risk_score"] <= 100.0
    assert body["risk_level"] in {"low", "medium", "high"}
    assert set(body["contributions"]) == {
        "route_deviation", "wandering", "confusion", "danger_zone", "unfamiliarity",
    }
    assert isinstance(body["gps_available"], bool)
    assert isinstance(body["wandering_detected"], bool)

    # A RiskScore row was persisted.
    latest = await crud.get_latest_risk_score(db_session, patient_id)
    assert latest is not None
    assert latest.score == body["risk_score"]
    assert latest.level == body["risk_level"]


async def test_risk_danger_zone_triggers_emergency_alert(client, db_session):
    patient_id = await _seed_patient_with_history(db_session)
    zone = DANGER_ZONES[0]

    # Force current location to a danger-zone centre via query override.
    resp = await client.get(
        f"/api/risk/{patient_id}",
        params={"lat": zone["latitude"], "lng": zone["longitude"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["emergency"] is True
    assert body["reason"] == "danger_zone"

    # A geofence alert was persisted.
    from sqlalchemy import select

    from app.db.models import Alert

    rows = (
        await db_session.execute(select(Alert).where(Alert.patient_id == patient_id))
    ).scalars().all()
    assert any(a.alert_type == "geofence" and a.severity == "critical" for a in rows)


async def test_risk_rejects_out_of_range_lat(client):
    resp = await client.get("/api/risk/1", params={"lat": 200, "lng": 100})
    assert resp.status_code == 422
