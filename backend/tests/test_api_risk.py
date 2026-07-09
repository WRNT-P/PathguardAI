"""End-to-end test of the Module 3 /api/risk endpoint.

Exercises the full pipeline: fetch (DB) -> collect raw factors (Module 2
detectors, sklearn — no TensorFlow) -> normalize -> score -> decide -> persist
RiskScore/Alert -> respond. Runs against the in-memory SQLite test DB.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import crud
from app.db.models import RiskScore
from app.mock.seed_risk_rules import SEED_DANGER_ZONES

pytestmark = pytest.mark.asyncio


async def _seed_scores(db, patient_id, scores):
    """Insert prior risk_scores oldest->newest (scores[-1] is most recent)."""
    now = datetime.now(timezone.utc)
    n = len(scores)
    for i, s in enumerate(scores):
        level = "low" if s < 50 else "medium" if s < 80 else "high"
        db.add(RiskScore(
            patient_id=patient_id, score=s, level=level,
            wandering_detected=False, gps_available=True,
            calculated_at=now - timedelta(minutes=(n - i) * 2),
        ))
    await db.commit()

# Two known places, both clear of the seeded danger zones.
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
    zone = SEED_DANGER_ZONES[0]

    # Force current location to a danger-zone centre via query override.
    resp = await client.get(
        f"/api/risk/{patient_id}",
        params={"lat": zone["center_latitude"], "lng": zone["center_longitude"]},
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


# ── Temporal rules (Improvement B) ────────────────────────────────────────────

async def test_temporal_cold_start_no_adjustment(client, db_session):
    """A patient with no score history: temporal rules are a no-op (parity)."""
    patient_id = await _seed_patient_with_history(db_session)

    body = (await client.get(f"/api/risk/{patient_id}")).json()
    assert body["temporal_rules_triggered"] == []
    assert body["temporal_adjustment"] == 0.0
    assert body["risk_score"] == body["base_risk_score"]


async def test_temporal_trend_adds_boost(client, db_session):
    """Four rising prior scores -> trend_escalation adds +10 to the current."""
    patient_id = await _seed_patient_with_history(db_session)
    await _seed_scores(db_session, patient_id, [10.0, 20.0, 30.0, 40.0])

    body = (await client.get(f"/api/risk/{patient_id}")).json()
    assert "trend_escalation" in body["temporal_rules_triggered"]
    assert body["temporal_adjustment"] == 10.0
    assert body["risk_score"] == round(body["base_risk_score"] + 10.0, 1)


async def test_temporal_sustained_forces_emergency(client, db_session):
    """Five sustained medium scores (4 prior + current) -> sustained_risk emergency."""
    patient_id = await _seed_patient_with_history(db_session)
    await _seed_scores(db_session, patient_id, [55.0, 55.0, 55.0, 55.0])

    # Far-from-route override so the CURRENT score is medium (>=50) but not a
    # danger zone and not >80 — isolating the sustained-risk trigger.
    body = (await client.get(
        f"/api/risk/{patient_id}", params={"lat": 13.7800, "lng": 100.5800}
    )).json()
    assert body["base_risk_score"] >= 50.0, body
    assert body["emergency"] is True
    assert body["reason"] == "sustained_risk"
    assert "sustained_high_risk" in body["temporal_rules_triggered"]
    # Equal (non-rising) history must NOT trigger the trend rule.
    assert "trend_escalation" not in body["temporal_rules_triggered"]
