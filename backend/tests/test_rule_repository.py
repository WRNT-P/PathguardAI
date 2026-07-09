"""Rule knowledge base — repository behavior and invariants.

Covers: seed integrity (weights sum to 1.0), the one-active-row-per-name
versioning invariant, strict weight-sum rejection (Q2), atomic audit writes
(Q4), and name/type validation (Q1).
"""
import math

import pytest
from sqlalchemy import select

from app.db import rule_repository as repo
from app.db.models import RiskFactorWeight, RiskThreshold, RuleAuditLog

pytestmark = pytest.mark.asyncio


async def test_seeded_weights_sum_to_one(db_session):
    weights = await repo.get_active_weights(db_session)
    assert set(weights) == repo.KNOWN_FACTORS
    assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9)


async def test_seeded_thresholds_complete(db_session):
    thresholds = await repo.get_all_thresholds(db_session)
    assert set(thresholds) == repo.KNOWN_THRESHOLDS
    assert thresholds[repo.EMERGENCY_SCORE] == 80.0
    assert thresholds[repo.GPS_GAP_SECONDS] == 600.0


async def test_seeded_danger_zones(db_session):
    zones = await repo.get_active_danger_zones(db_session)
    assert len(zones) == 2
    assert {z["zone_type"] for z in zones} == {"highway", "waterway"}


async def test_update_threshold_versions_and_audits(db_session):
    await repo.update_threshold(db_session, repo.EMERGENCY_SCORE, 70.0,
                                changed_by="test", reason="lower the bar")
    await db_session.commit()

    # New value visible; exactly one active row; version bumped; audit written.
    assert await repo.get_threshold(db_session, repo.EMERGENCY_SCORE) == 70.0
    rows = (await db_session.execute(
        select(RiskThreshold)
        .where(RiskThreshold.threshold_name == repo.EMERGENCY_SCORE)
        .order_by(RiskThreshold.version)
    )).scalars().all()
    assert [r.active for r in rows] == [False, True]
    assert [r.version for r in rows] == [1, 2]
    assert rows[1].source_reference == rows[0].source_reference  # citation carried over

    audit = (await db_session.execute(select(RuleAuditLog))).scalars().all()
    assert len(audit) == 1
    assert audit[0].table_name == "risk_thresholds"
    assert audit[0].record_id == rows[1].id
    assert (audit[0].old_value, audit[0].new_value) == ("80.0", "70.0")
    assert audit[0].reason == "lower the bar"


async def test_update_weight_rejects_broken_sum(db_session):
    with pytest.raises(repo.RuleValidationError, match="does not renormalize"):
        await repo.update_weight(db_session, "wandering", 0.50,
                                 changed_by="test", reason="oops")
    # Nothing changed, nothing audited.
    weights = await repo.get_active_weights(db_session)
    assert weights["wandering"] == 0.25
    assert (await db_session.execute(select(RuleAuditLog))).scalars().all() == []


async def test_update_weights_batch_rebalance(db_session):
    await repo.update_weights(
        db_session, {"route_deviation": 0.35, "wandering": 0.20},
        changed_by="test", reason="rebalance toward deviation")
    await db_session.commit()

    weights = await repo.get_active_weights(db_session)
    assert weights["route_deviation"] == 0.35
    assert weights["wandering"] == 0.20
    assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9)

    # Exactly one active row per factor survives the rebalance.
    for factor in ("route_deviation", "wandering"):
        active = (await db_session.execute(
            select(RiskFactorWeight)
            .where(RiskFactorWeight.factor_name == factor, RiskFactorWeight.active)
        )).scalars().all()
        assert len(active) == 1
    assert len((await db_session.execute(select(RuleAuditLog))).scalars().all()) == 2


async def test_unknown_names_rejected(db_session):
    with pytest.raises(repo.RuleValidationError):
        await repo.update_weight(db_session, "moon_phase", 0.1,
                                 changed_by="test", reason="nope")
    with pytest.raises(repo.RuleValidationError):
        await repo.update_threshold(db_session, "not_a_threshold", 1.0,
                                    changed_by="test", reason="nope")
    with pytest.raises(repo.RuleValidationError):
        await repo.get_threshold(db_session, "not_a_threshold")


async def test_empty_reason_rejected(db_session):
    with pytest.raises(repo.RuleValidationError, match="reason"):
        await repo.update_threshold(db_session, repo.EMERGENCY_SCORE, 70.0,
                                    changed_by="test", reason="   ")


async def test_danger_zone_add_and_deactivate(db_session):
    zone_id = await repo.add_danger_zone(
        db_session, name="Construction pit", latitude=13.75, longitude=100.51,
        radius_m=80.0, zone_type="construction",
        source_reference="site survey", rationale="open excavation",
        changed_by="test", reason="new hazard reported")
    await db_session.commit()

    zones = await repo.get_active_danger_zones(db_session)
    assert any(z["id"] == zone_id for z in zones)

    await repo.deactivate_danger_zone(db_session, zone_id,
                                      changed_by="test", reason="site closed")
    await db_session.commit()
    zones = await repo.get_active_danger_zones(db_session)
    assert not any(z["id"] == zone_id for z in zones)

    audit = (await db_session.execute(
        select(RuleAuditLog).order_by(RuleAuditLog.id)
    )).scalars().all()
    assert [a.table_name for a in audit] == ["danger_zones", "danger_zones"]
    assert audit[1].field_changed == "active"


async def test_danger_zone_bad_type_rejected(db_session):
    with pytest.raises(repo.RuleValidationError, match="zone_type"):
        await repo.add_danger_zone(
            db_session, name="x", latitude=0.0, longitude=0.0, radius_m=10.0,
            zone_type="volcano", source_reference="s", rationale="r",
            changed_by="test", reason="nope")
