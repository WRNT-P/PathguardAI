"""Judge-facing admin endpoints — /api/admin/rules and /api/admin/rules/history.

Integration tests over the real router + seeded in-memory DB (Q13a): every
rule must surface its medical source and rationale, and the audit history
must come back newest-first and paginated.
"""
import pytest

from app.db import rule_repository as repo

pytestmark = pytest.mark.asyncio


async def test_rules_lists_all_active_rules_with_citations(client):
    resp = await client.get("/api/admin/rules")
    assert resp.status_code == 200
    body = resp.json()

    assert {w["factor_name"] for w in body["weights"]} == set(repo.KNOWN_FACTORS)
    assert {t["threshold_name"] for t in body["thresholds"]} == set(repo.KNOWN_THRESHOLDS)
    assert len(body["danger_zones"]) == 2

    # The instructor's requirement: every rule carries source + rationale.
    for rule in body["weights"] + body["thresholds"] + body["danger_zones"]:
        assert rule["source_reference"].strip()
        assert rule["rationale"].strip()

    # Weights are the seeded values and sum to 1.0.
    weights = {w["factor_name"]: w["weight"] for w in body["weights"]}
    assert weights["route_deviation"] == 0.30
    assert abs(sum(weights.values()) - 1.0) < 1e-9


async def test_history_empty_before_any_change(client):
    resp = await client.get("/api/admin/rules/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == []
    assert body["limit"] == 100


async def test_history_newest_first_and_paginated(client, db_session):
    await repo.update_threshold(db_session, repo.EMERGENCY_SCORE, 70.0,
                                changed_by="test", reason="first change")
    await db_session.commit()
    await repo.update_threshold(db_session, repo.EMERGENCY_SCORE, 75.0,
                                changed_by="test", reason="second change")
    await db_session.commit()

    resp = await client.get("/api/admin/rules/history")
    body = resp.json()
    assert body["total_returned"] == 2
    assert body["entries"][0]["reason"] == "second change"   # newest first
    assert body["entries"][1]["reason"] == "first change"
    assert body["entries"][0]["old_value"] == "70.0"
    assert body["entries"][0]["new_value"] == "75.0"

    resp = await client.get("/api/admin/rules/history", params={"limit": 1})
    body = resp.json()
    assert body["total_returned"] == 1
    assert body["entries"][0]["reason"] == "second change"


async def test_rule_change_shows_in_rules_endpoint(client, db_session):
    """The admin view reflects a KB change immediately (no cache)."""
    await repo.update_threshold(db_session, repo.GPS_GAP_SECONDS, 300.0,
                                changed_by="test", reason="tighten gap window")
    await db_session.commit()

    resp = await client.get("/api/admin/rules")
    thresholds = {t["threshold_name"]: t for t in resp.json()["thresholds"]}
    assert thresholds["gps_gap_seconds"]["value"] == 300.0
    assert thresholds["gps_gap_seconds"]["version"] == 2
