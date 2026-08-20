"""Phase 5 — danger zones, the one risk factor that works on day one.

``danger_zone`` is 15% of the score and needs no profile, no history and no fitted
model. Until this router existed the table had no writer outside the demo seeder,
so that 15% was zero for every real patient.
"""
import pytest
from sqlalchemy import select

from app.db import rule_repository
from app.db.models import DangerZone, RuleAuditLog

pytestmark = pytest.mark.asyncio

CANAL = {
    "name": "คลองหลังหมู่บ้าน",
    "center_latitude": 13.7550,
    "center_longitude": 100.5000,
    "radius_meters": 120,
    "zone_type": "waterway",
    "rationale": "เคยมีคนตกเมื่อปีที่แล้ว ไม่มีราวกั้น",
}


async def test_created_zone_is_visible_to_the_scorer(client, db_session):
    resp = await client.post("/api/danger-zones", json=CANAL)
    assert resp.status_code == 201
    body = resp.json()
    assert body["active"] is True
    assert body["zone_type"] == "waterway"

    # The seeded KB zones are already there; ours has to be among what the risk
    # pipeline actually loads, not merely in the table.
    zones = await rule_repository.get_active_danger_zones(db_session)
    assert any(z["id"] == body["id"] and z["radius_m"] == 120 for z in zones)


async def test_creation_is_written_to_the_audit_trail(client, db_session):
    zone_id = (await client.post("/api/danger-zones", json=CANAL)).json()["id"]

    rows = (await db_session.execute(
        select(RuleAuditLog).where(
            RuleAuditLog.table_name == "danger_zones",
            RuleAuditLog.record_id == zone_id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason  # design Q4: no rule change without a stated reason


async def test_rationale_is_required(client):
    payload = {k: v for k, v in CANAL.items() if k != "rationale"}
    assert (await client.post("/api/danger-zones", json=payload)).status_code == 422
    assert (await client.post(
        "/api/danger-zones", json={**CANAL, "rationale": ""})).status_code == 422


async def test_zone_type_is_restricted_to_the_known_set(client):
    resp = await client.post("/api/danger-zones", json={**CANAL, "zone_type": "cliff"})
    assert resp.status_code == 422


async def test_negative_radius_is_rejected(client):
    resp = await client.post("/api/danger-zones", json={**CANAL, "radius_meters": 0})
    assert resp.status_code == 422


async def test_listing_returns_only_active_zones(client):
    zone_id = (await client.post("/api/danger-zones", json=CANAL)).json()["id"]
    ids = [z["id"] for z in (await client.get("/api/danger-zones")).json()]
    assert zone_id in ids

    assert (await client.delete(f"/api/danger-zones/{zone_id}")).status_code == 204
    ids_after = [z["id"] for z in (await client.get("/api/danger-zones")).json()]
    assert zone_id not in ids_after


async def test_delete_deactivates_rather_than_destroys(client, db_session):
    """A zone that was live during an incident stays part of the record of it."""
    zone_id = (await client.post("/api/danger-zones", json=CANAL)).json()["id"]
    await client.delete(f"/api/danger-zones/{zone_id}")

    zone = (await db_session.execute(
        select(DangerZone).where(DangerZone.id == zone_id))).scalar_one()
    assert zone.active is False
    assert zone.name == CANAL["name"]


async def test_deleting_an_unknown_zone_is_a_404(client):
    assert (await client.delete("/api/danger-zones/999999")).status_code == 404
