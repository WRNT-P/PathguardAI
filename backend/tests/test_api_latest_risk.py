"""GET /api/patients/{id}/risk/latest — the read the caregiver's map can poll.

The point of this endpoint is what it does *not* do. ``GET /api/risk/{id}``
recomputes, writes a ``risk_scores`` row and can push; a live map screen puts a
timer on whatever it is given, and a timer on that endpoint fills the five-row
window ``sustained_high_risk`` reads in seconds. So the load-bearing assertions
here are the ones proving nothing was written.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db import crud
from app.db.models import Alert, RiskScore

pytestmark = pytest.mark.asyncio


async def _patient(db, uid="latest-risk-uid"):
    user = await crud.create_user(db, firebase_uid=uid, name="Pat", role="patient")
    await db.commit()
    return user.id


async def _add_score(db, patient_id, score, level, *, minutes_ago=0, factors=None):
    db.add(RiskScore(
        patient_id=patient_id, score=score, level=level,
        wandering_detected=score >= 50, gps_available=True,
        factors=factors,
        calculated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    ))
    await db.commit()


async def _count(db, model, patient_id):
    return (await db.execute(
        select(func.count()).select_from(model).where(model.patient_id == patient_id)
    )).scalar_one()


async def test_unknown_patient_is_404_not_no_data(client):
    # Mirrors /track: silence for a wrong id would read as "not scored yet".
    resp = await client.get("/api/patients/999999/risk/latest")
    assert resp.status_code == 404


async def test_known_patient_with_no_score_is_200_no_data(client, db_session):
    patient_id = await _patient(db_session, "no-score-uid")

    resp = await client.get(f"/api/patients/{patient_id}/risk/latest")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_data"
    assert body["risk_score"] is None
    assert body["calculated_at"] is None


async def test_returns_the_newest_row_not_the_first(client, db_session):
    patient_id = await _patient(db_session, "newest-uid")
    await _add_score(db_session, patient_id, 12.0, "low", minutes_ago=30)
    await _add_score(db_session, patient_id, 63.5, "medium", minutes_ago=1)

    body = (await client.get(f"/api/patients/{patient_id}/risk/latest")).json()

    assert body["status"] == "ok"
    assert body["risk_score"] == 63.5
    assert body["risk_level"] == "medium"
    assert body["wandering_detected"] is True


async def test_polling_writes_nothing_and_alerts_nothing(client, db_session):
    """The whole reason this endpoint exists.

    Ten calls in a row must leave the row count untouched. Against
    ``GET /api/risk`` the same ten calls would add ten rows, which is what fills
    the ``sustained_high_risk`` window early.
    """
    patient_id = await _patient(db_session, "poll-uid")
    await _add_score(db_session, patient_id, 55.0, "medium")

    scores_before = await _count(db_session, RiskScore, patient_id)
    alerts_before = await _count(db_session, Alert, patient_id)

    for _ in range(10):
        assert (await client.get(f"/api/patients/{patient_id}/risk/latest")).status_code == 200

    assert await _count(db_session, RiskScore, patient_id) == scores_before
    assert await _count(db_session, Alert, patient_id) == alerts_before


async def test_carries_calculated_at_as_utc_z(client, db_session):
    # A score with no age on a live map reads as current however old it is.
    patient_id = await _patient(db_session, "age-uid")
    await _add_score(db_session, patient_id, 20.0, "low", minutes_ago=5)

    body = (await client.get(f"/api/patients/{patient_id}/risk/latest")).json()

    assert body["calculated_at"].endswith("Z")
    datetime.fromisoformat(body["calculated_at"].replace("Z", "+00:00"))


async def test_contributions_come_back_parsed(client, db_session):
    patient_id = await _patient(db_session, "factors-uid")
    await _add_score(db_session, patient_id, 40.0, "low",
                     factors=json.dumps({"wandering": 0.25, "danger_zone": 0.0}))

    body = (await client.get(f"/api/patients/{patient_id}/risk/latest")).json()

    assert body["contributions"] == {"wandering": 0.25, "danger_zone": 0.0}


async def test_malformed_factors_degrade_instead_of_500(client, db_session):
    # A bad JSON string in one column must not take the caregiver's map down.
    patient_id = await _patient(db_session, "badjson-uid")
    await _add_score(db_session, patient_id, 40.0, "low", factors="{not json")

    resp = await client.get(f"/api/patients/{patient_id}/risk/latest")

    assert resp.status_code == 200
    assert resp.json()["contributions"] is None
    assert resp.json()["risk_score"] == 40.0


async def test_does_not_report_emergency_or_reason(client, db_session):
    """A stored row's emergency was decided and acted on when it was written.

    Re-surfacing it on every poll would let a caregiver read a handled
    emergency as a live one, so the shape deliberately cannot carry it.
    """
    patient_id = await _patient(db_session, "no-emergency-uid")
    await _add_score(db_session, patient_id, 92.0, "high")

    body = (await client.get(f"/api/patients/{patient_id}/risk/latest")).json()

    assert "emergency" not in body
    assert "reason" not in body
