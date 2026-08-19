"""API endpoint tests via httpx ASGITransport (no live Postgres/Firebase)."""
import pytest

from app.db import crud

pytestmark = pytest.mark.asyncio


async def test_root(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "PathGuard AI"


async def test_register_user_created(client):
    resp = await client.post(
        "/api/register",
        json={"firebase_uid": "fb-123", "name": "Alice", "role": "patient"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["firebase_uid"] == "fb-123"
    assert body["role"] == "patient"
    assert isinstance(body["id"], int)


async def test_register_duplicate_uid_conflicts(client):
    payload = {"firebase_uid": "dup", "name": "Bob", "role": "caregiver"}
    first = await client.post("/api/register", json=payload)
    assert first.status_code == 201
    second = await client.post("/api/register", json=payload)
    assert second.status_code == 409


async def test_register_invalid_role_rejected(client):
    resp = await client.post(
        "/api/register",
        json={"firebase_uid": "x", "name": "X", "role": "doctor"},
    )
    assert resp.status_code == 422  # role must be patient|caregiver


async def _register_patient(client, uid="gps-patient") -> int:
    """Register a patient and return the int ``users.id`` GPS readings need."""
    resp = await client.post(
        "/api/register", json={"firebase_uid": uid, "name": "P", "role": "patient"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_gps_endpoint_persists_reading(client, db_session):
    patient_id = await _register_patient(client)

    resp = await client.post(
        "/api/gps",
        json={
            "patient_id": patient_id,
            "latitude": 13.75,
            "longitude": 100.50,
            "speed": 1.2,
            "direction": 90.0,
            "recorded_at": "2026-06-12T13:00:00Z",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success", "patient_id": patient_id, "accepted": 1,
    }

    # The point reached PostgreSQL via gps_processor, Kalman-smoothed — the whole
    # reason this endpoint exists. A stub that only answers 200 would pass the
    # assertions above but fail here.
    rows = await crud.get_gps_history(db_session, patient_id, days=3650)
    assert len(rows) == 1
    assert rows[0].latitude == 13.75
    assert rows[0].smooth_latitude is not None


async def test_gps_endpoint_rejects_out_of_range_latitude(client):
    resp = await client.post(
        "/api/gps",
        json={
            "patient_id": 1,
            "latitude": 999.0,
            "longitude": 100.50,
            "recorded_at": "2026-06-12T13:00:00Z",
        },
    )
    assert resp.status_code == 422


async def test_gps_endpoint_rejects_unregistered_patient(client):
    """Gotcha #9: the app must call /api/register first — say so, don't 500."""
    resp = await client.post(
        "/api/gps",
        json={
            "patient_id": 999999,
            "latitude": 13.75,
            "longitude": 100.50,
            "recorded_at": "2026-06-12T13:00:00Z",
        },
    )
    assert resp.status_code == 404
    assert "register" in resp.json()["detail"]


async def test_gps_batch_persists_points_in_time_order(client, db_session):
    patient_id = await _register_patient(client, uid="gps-batch")

    # Sent newest-first, as an offline queue flushed LIFO would arrive.
    resp = await client.post(
        "/api/gps/batch",
        json={
            "points": [
                {
                    "patient_id": patient_id,
                    "latitude": 13.77,
                    "longitude": 100.52,
                    "recorded_at": "2026-06-12T13:02:00Z",
                },
                {
                    "patient_id": patient_id,
                    "latitude": 13.75,
                    "longitude": 100.50,
                    "recorded_at": "2026-06-12T13:00:00Z",
                },
                {
                    "patient_id": patient_id,
                    "latitude": 13.76,
                    "longitude": 100.51,
                    "recorded_at": "2026-06-12T13:01:00Z",
                },
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 3

    rows = await crud.get_gps_history(db_session, patient_id, days=3650)
    assert [r.latitude for r in rows] == [13.75, 13.76, 13.77]


async def test_gps_batch_rejects_mixed_patients(client):
    patient_id = await _register_patient(client, uid="gps-mixed")
    resp = await client.post(
        "/api/gps/batch",
        json={
            "points": [
                {
                    "patient_id": patient_id,
                    "latitude": 13.75,
                    "longitude": 100.50,
                    "recorded_at": "2026-06-12T13:00:00Z",
                },
                {
                    "patient_id": patient_id + 1,
                    "latitude": 13.76,
                    "longitude": 100.51,
                    "recorded_at": "2026-06-12T13:01:00Z",
                },
            ]
        },
    )
    assert resp.status_code == 422


async def test_gps_batch_rejects_empty_payload(client):
    resp = await client.post("/api/gps/batch", json={"points": []})
    assert resp.status_code == 422
