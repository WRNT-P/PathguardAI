# pathguard/backend/app/api/admin_rules.py
"""Rule knowledge-base inspection endpoints (read-only).

Lets judges/caregivers see every active rule with its medical source and
rationale, plus the full audit trail of rule changes.

Public by design (Q6): these GETs expose the least-sensitive data in the
system — the rules are exactly what we WANT inspected, and no other endpoint
is auth-gated in this demo. A production deployment would gate this router
(and any future rule-update POST) behind caregiver-admin auth.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services.auth import Caller, current_caller
from app.db.models import (
    DangerZone, RiskFactorWeight, RiskThreshold, RuleAuditLog, TemporalRule,
)

router = APIRouter()


class WeightRule(BaseModel):
    factor_name: str
    weight: float
    version: int
    source_reference: str
    rationale: str
    effective_from: datetime


class ThresholdRule(BaseModel):
    threshold_name: str
    value: float
    unit: str
    version: int
    source_reference: str
    rationale: str
    effective_from: datetime


class DangerZoneRule(BaseModel):
    id: int
    name: str
    center_latitude: float
    center_longitude: float
    radius_meters: float
    zone_type: str
    source_reference: str
    rationale: str
    effective_from: datetime


class TemporalRuleView(BaseModel):
    rule_name: str
    parameters: dict
    version: int
    source_reference: str
    rationale: str
    effective_from: datetime


class RulesResponse(BaseModel):
    weights: list[WeightRule]
    thresholds: list[ThresholdRule]
    danger_zones: list[DangerZoneRule]
    temporal_rules: list[TemporalRuleView]


class AuditEntry(BaseModel):
    id: int
    table_name: str
    record_id: int
    field_changed: str
    old_value: str | None
    new_value: str | None
    changed_by: str
    changed_at: datetime
    reason: str


class AuditHistoryResponse(BaseModel):
    total_returned: int
    limit: int
    offset: int
    entries: list[AuditEntry]


@router.get(
    "/api/admin/rules",
    response_model=RulesResponse,
    summary="All active risk rules with medical sources and rationale",
)
async def get_rules(
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(current_caller),
) -> RulesResponse:
    weights = (await db.execute(
        select(RiskFactorWeight)
        .where(RiskFactorWeight.active)
        .order_by(RiskFactorWeight.weight.desc())
    )).scalars().all()
    thresholds = (await db.execute(
        select(RiskThreshold)
        .where(RiskThreshold.active)
        .order_by(RiskThreshold.threshold_name)
    )).scalars().all()
    zones = (await db.execute(
        select(DangerZone).where(DangerZone.active).order_by(DangerZone.id)
    )).scalars().all()
    temporal = (await db.execute(
        select(TemporalRule).where(TemporalRule.active).order_by(TemporalRule.rule_name)
    )).scalars().all()

    return RulesResponse(
        weights=[WeightRule.model_validate(w, from_attributes=True) for w in weights],
        thresholds=[ThresholdRule.model_validate(t, from_attributes=True) for t in thresholds],
        danger_zones=[DangerZoneRule.model_validate(z, from_attributes=True) for z in zones],
        temporal_rules=[TemporalRuleView.model_validate(t, from_attributes=True) for t in temporal],
    )


@router.get(
    "/api/admin/rules/history",
    response_model=AuditHistoryResponse,
    summary="Audit trail of rule changes, newest first",
)
async def get_rules_history(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(current_caller),
) -> AuditHistoryResponse:
    rows = (await db.execute(
        select(RuleAuditLog)
        .order_by(RuleAuditLog.changed_at.desc(), RuleAuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )).scalars().all()
    entries = [AuditEntry.model_validate(r, from_attributes=True) for r in rows]
    return AuditHistoryResponse(
        total_returned=len(entries), limit=limit, offset=offset, entries=entries,
    )
