"""DB CRUD layer — integration tests against in-memory SQLite.

These exercise the real SQLAlchemy models + crud helpers. crud helpers flush
but never commit (the caller owns the tx), so tests commit explicitly.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import crud

pytestmark = pytest.mark.asyncio


async def _make_patient(db, firebase_uid="uid-1", name="Alice"):
    user = await crud.create_user(db, firebase_uid=firebase_uid, name=name, role="patient")
    await db.commit()
    return user


async def test_create_and_resolve_user(db_session):
    user = await _make_patient(db_session)
    assert user.id is not None
    resolved = await crud.get_user_id_by_firebase_uid(db_session, "uid-1")
    assert resolved == user.id


async def test_resolve_unknown_uid_returns_none(db_session):
    assert await crud.get_user_id_by_firebase_uid(db_session, "nope") is None


async def test_save_and_get_gps_history_ordered_oldest_first(db_session):
    user = await _make_patient(db_session)
    now = datetime.now(timezone.utc)
    # Insert newest first to prove ordering is by recorded_at.
    await crud.save_gps_point(db_session, user.id, 13.75, 100.50, recorded_at=now)
    await crud.save_gps_point(
        db_session, user.id, 13.70, 100.40, recorded_at=now - timedelta(hours=2)
    )
    await db_session.commit()

    history = await crud.get_gps_history(db_session, user.id)
    assert len(history) == 2
    assert history[0].recorded_at < history[1].recorded_at  # oldest first


async def test_get_gps_history_excludes_old_points(db_session):
    user = await _make_patient(db_session)
    now = datetime.now(timezone.utc)
    await crud.save_gps_point(db_session, user.id, 1.0, 2.0, recorded_at=now)
    await crud.save_gps_point(
        db_session, user.id, 3.0, 4.0, recorded_at=now - timedelta(days=40)
    )
    await db_session.commit()

    history = await crud.get_gps_history(db_session, user.id, days=30)
    assert len(history) == 1
    assert history[0].latitude == 1.0


async def test_get_latest_gps(db_session):
    user = await _make_patient(db_session)
    now = datetime.now(timezone.utc)
    await crud.save_gps_point(
        db_session, user.id, 1.0, 1.0, recorded_at=now - timedelta(minutes=5)
    )
    await crud.save_gps_point(db_session, user.id, 9.0, 9.0, recorded_at=now)
    await db_session.commit()

    latest = await crud.get_latest_gps(db_session, user.id)
    assert latest.latitude == 9.0


async def test_get_latest_gps_none_when_empty(db_session):
    user = await _make_patient(db_session)
    assert await crud.get_latest_gps(db_session, user.id) is None


async def test_save_and_get_latest_risk_score(db_session):
    user = await _make_patient(db_session)
    await crud.save_risk_score(
        db_session, user.id, score=72.5, level="medium", wandering_detected=True,
        factors='{"route_deviation": 30.0}',
    )
    await db_session.commit()

    latest = await crud.get_latest_risk_score(db_session, user.id)
    assert latest.score == 72.5
    assert latest.level == "medium"
    assert latest.wandering_detected is True


async def test_save_alert(db_session):
    user = await _make_patient(db_session)
    alert = await crud.save_alert(
        db_session, user.id, alert_type="geofence", severity="critical",
        message="entered danger zone", latitude=13.75, longitude=100.5,
    )
    await db_session.commit()
    assert alert.id is not None
    assert alert.alert_type == "geofence"


async def test_upsert_behavioral_profile_creates_then_updates(db_session):
    user = await _make_patient(db_session)
    p1 = await crud.upsert_behavioral_profile(
        db_session, user.id, known_places='[{"cluster_id": 0}]', typical_range_km=2.0
    )
    await db_session.commit()
    assert p1.known_places == '[{"cluster_id": 0}]'

    # Update only routine_patterns; known_places must persist (None args skip).
    p2 = await crud.upsert_behavioral_profile(
        db_session, user.id, routine_patterns='[{"hour": 8}]'
    )
    await db_session.commit()
    assert p2.id == p1.id  # same row (one per patient)
    assert p2.known_places == '[{"cluster_id": 0}]'
    assert p2.routine_patterns == '[{"hour": 8}]'
    assert p2.typical_range_km == 2.0
