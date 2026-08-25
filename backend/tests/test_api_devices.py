"""``POST /api/devices/token`` — where the caregiver's phone becomes reachable.

Without a row here an alert is written and goes nowhere, so this endpoint is the
one piece of Phase 4 the Flutter app cannot skip.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models import DeviceToken

pytestmark = pytest.mark.asyncio


async def _register(client, uid: str, role: str = "caregiver") -> int:
    resp = await client.post(
        "/api/register", json={"firebase_uid": uid, "name": "C", "role": role}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_registering_a_token_stores_it(client, db_session):
    user_id = await _register(client, "cg-1")

    resp = await client.post("/api/devices/token", json={
        "user_id": user_id, "token": "tok-abc-123456", "platform": "android",
    })

    assert resp.status_code == 200
    assert resp.json()["user_id"] == user_id
    assert await db_session.scalar(
        select(func.count()).select_from(DeviceToken)) == 1


async def test_relaunching_the_app_does_not_pile_up_rows(client, db_session):
    """The app re-POSTs its token on every launch — that must be an upsert."""
    user_id = await _register(client, "cg-2")
    body = {"user_id": user_id, "token": "tok-same-000000", "platform": "android"}

    await client.post("/api/devices/token", json=body)
    await client.post("/api/devices/token", json=body)

    assert await db_session.scalar(
        select(func.count()).select_from(DeviceToken)) == 1


async def test_a_shared_phone_repoints_at_whoever_signed_in_last(client, db_session):
    """Firebase hands the same token to a second account on the same device; the
    token, not the user, is the identity — otherwise the unique index rejects it
    and the new caregiver silently gets no alerts."""
    first = await _register(client, "cg-3")
    second = await _register(client, "cg-4")

    await client.post("/api/devices/token", json={
        "user_id": first, "token": "tok-shared-9999", "platform": "android"})
    resp = await client.post("/api/devices/token", json={
        "user_id": second, "token": "tok-shared-9999", "platform": "android"})

    assert resp.status_code == 200
    assert resp.json()["user_id"] == second
    assert await db_session.scalar(
        select(func.count()).select_from(DeviceToken)) == 1


async def test_an_unknown_user_is_a_404_not_an_fk_crash(client):
    resp = await client.post("/api/devices/token", json={
        "user_id": 999999, "token": "tok-nobody-1234", "platform": "android"})

    assert resp.status_code == 404
    assert "register" in resp.json()["detail"]
