"""API endpoint tests via httpx ASGITransport (no live Postgres/Firebase)."""
import pytest

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


async def test_gps_endpoint_accepts_reading(client):
    resp = await client.post(
        "/api/gps",
        json={
            "patient_id": "fb-123",
            "latitude": 13.75,
            "longitude": 100.50,
            "speed": 1.2,
            "direction": 90.0,
            "timestamp": "2026-06-12T13:00:00Z",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "patient_id": "fb-123"}


async def test_gps_endpoint_rejects_out_of_range_latitude(client):
    resp = await client.post(
        "/api/gps",
        json={
            "patient_id": "fb-123",
            "latitude": 999.0,
            "longitude": 100.50,
            "timestamp": "2026-06-12T13:00:00Z",
        },
    )
    assert resp.status_code == 422
