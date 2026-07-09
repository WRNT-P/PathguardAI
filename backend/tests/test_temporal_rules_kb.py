"""Temporal-rules knowledge base — seed integrity, versioning, audit.

Mirrors test_rule_repository.py's discipline for the new temporal_rules table.
"""
import pytest
from sqlalchemy import select

from app.db import rule_repository as repo
from app.db.models import RuleAuditLog, TemporalRule

pytestmark = pytest.mark.asyncio


async def test_seeded_temporal_rules_present(db_session):
    rules = await repo.get_active_temporal_rules(db_session)
    by_name = {r["rule_name"]: r for r in rules}
    assert set(by_name) == repo.KNOWN_TEMPORAL_RULES
    # Parameters + citations survived the round-trip through the DB.
    assert by_name["trend_escalation"]["parameters"] == {"window": 3, "boost": 10.0}
    assert by_name["sustained_high_risk"]["parameters"] == {"window": 5, "min_score": 50.0}
    for r in rules:
        assert r["source_reference"].strip()
        assert r["rationale"].strip()


async def test_update_versions_and_audits(db_session):
    await repo.update_temporal_rule(
        db_session, repo.TREND_ESCALATION, {"window": 3, "boost": 15.0},
        changed_by="test", reason="stronger trend boost")
    await db_session.commit()

    rows = (await db_session.execute(
        select(TemporalRule)
        .where(TemporalRule.rule_name == repo.TREND_ESCALATION)
        .order_by(TemporalRule.version)
    )).scalars().all()
    assert [r.active for r in rows] == [False, True]
    assert [r.version for r in rows] == [1, 2]
    assert rows[1].parameters["boost"] == 15.0
    assert rows[1].source_reference == rows[0].source_reference  # citation carried

    # Exactly one active row per name.
    active = await repo.get_active_temporal_rules(db_session)
    assert sum(1 for r in active if r["rule_name"] == repo.TREND_ESCALATION) == 1

    audit = (await db_session.execute(select(RuleAuditLog))).scalars().all()
    assert len(audit) == 1
    assert audit[0].table_name == "temporal_rules"
    assert audit[0].field_changed == "parameters"
    assert audit[0].reason == "stronger trend boost"


async def test_unknown_rule_rejected(db_session):
    with pytest.raises(repo.RuleValidationError):
        await repo.update_temporal_rule(db_session, "made_up_rule", {"x": 1},
                                        changed_by="test", reason="nope")


async def test_empty_params_rejected(db_session):
    with pytest.raises(repo.RuleValidationError):
        await repo.update_temporal_rule(db_session, repo.TREND_ESCALATION, {},
                                        changed_by="test", reason="nope")


async def test_empty_reason_rejected(db_session):
    with pytest.raises(repo.RuleValidationError, match="reason"):
        await repo.update_temporal_rule(
            db_session, repo.TREND_ESCALATION, {"window": 3, "boost": 10.0},
            changed_by="test", reason="  ")
