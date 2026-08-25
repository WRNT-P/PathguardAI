# pathguard/backend/app/db/rule_repository.py
"""Read/write access to the Module 3 rule knowledge base.

Reads are performed fresh per request (no cache — design Q3) so a rule change
is visible on the very next API call, including direct SQL edits made during a
demo.

Updates never mutate a rule row. The invariant — exactly one active row per
name at all times — is maintained by every update_* function doing, in ONE
session (design Q4: caller commits once, so all-or-nothing):
    1. flip the current active row to active=False
    2. insert a new row with version+1, active=True
    3. insert a RuleAuditLog row pointing at the new row
If any step fails the whole transaction rolls back and the old rule stays
active; the audit log can never disagree with rule state.

Row names are referenced through the module-level constants below (design
Q12) — never retype the strings at call sites.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DangerZone, RiskFactorWeight, RiskThreshold, RuleAuditLog, TemporalRule,
)

# ── Canonical rule names (Q12) ────────────────────────────────────────────────
KNOWN_FACTORS = frozenset(
    {"route_deviation", "wandering", "confusion", "danger_zone", "unfamiliarity"}
)

LOW_CEILING = "low_ceiling"
MEDIUM_CEILING = "medium_ceiling"
EMERGENCY_SCORE = "emergency_score"
ROUTE_DEVIATION_CEILING_M = "route_deviation_ceiling_m"
GPS_GAP_SECONDS = "gps_gap_seconds"
PUSH_COOLDOWN_SECONDS = "push_cooldown_seconds"
SOS_COOLDOWN_SECONDS = "sos_cooldown_seconds"
KNOWN_THRESHOLDS = frozenset(
    {LOW_CEILING, MEDIUM_CEILING, EMERGENCY_SCORE, ROUTE_DEVIATION_CEILING_M,
     GPS_GAP_SECONDS, PUSH_COOLDOWN_SECONDS, SOS_COOLDOWN_SECONDS}
)

ZONE_TYPES = frozenset({"highway", "waterway", "construction", "other"})

TREND_ESCALATION = "trend_escalation"
SUSTAINED_HIGH_RISK = "sustained_high_risk"
KNOWN_TEMPORAL_RULES = frozenset({TREND_ESCALATION, SUSTAINED_HIGH_RISK})

# Active weights must sum to 1.0 within this tolerance (Q2 — strict reject).
WEIGHT_SUM_TOLERANCE = 1e-3


class RuleValidationError(ValueError):
    """A rule update was rejected (unknown name, bad value, or broken sum)."""


# ── Reads (fresh per request — Q3) ────────────────────────────────────────────

async def get_active_weights(session: AsyncSession) -> dict[str, float]:
    """The five active factor weights as {factor_name: weight}."""
    rows = (await session.execute(
        select(RiskFactorWeight).where(RiskFactorWeight.active)
    )).scalars().all()
    weights = {r.factor_name: r.weight for r in rows}
    if set(weights) != KNOWN_FACTORS:
        missing = sorted(KNOWN_FACTORS - set(weights))
        raise RuntimeError(
            f"Rule KB is missing active weights for {missing} — "
            "run: python -m app.mock.seed_risk_rules"
        )
    return weights


async def get_all_thresholds(session: AsyncSession) -> dict[str, float]:
    """All active thresholds as {threshold_name: value}."""
    rows = (await session.execute(
        select(RiskThreshold).where(RiskThreshold.active)
    )).scalars().all()
    thresholds = {r.threshold_name: r.value for r in rows}
    if set(thresholds) != KNOWN_THRESHOLDS:
        missing = sorted(KNOWN_THRESHOLDS - set(thresholds))
        raise RuntimeError(
            f"Rule KB is missing active thresholds for {missing} — "
            "run: python -m app.mock.seed_risk_rules"
        )
    return thresholds


async def get_threshold(session: AsyncSession, name: str) -> float:
    """One active threshold value by name (use the module constants)."""
    if name not in KNOWN_THRESHOLDS:
        raise RuleValidationError(f"Unknown threshold {name!r}")
    row = (await session.execute(
        select(RiskThreshold)
        .where(RiskThreshold.threshold_name == name, RiskThreshold.active)
    )).scalar_one_or_none()
    if row is None:
        raise RuntimeError(
            f"No active threshold {name!r} — run: python -m app.mock.seed_risk_rules"
        )
    return row.value


async def get_active_danger_zones(session: AsyncSession) -> list[dict]:
    """Active danger zones as plain dicts (the shape the pure ai/ layer takes)."""
    rows = (await session.execute(
        select(DangerZone).where(DangerZone.active)
    )).scalars().all()
    return [
        {"id": z.id, "name": z.name, "latitude": z.center_latitude,
         "longitude": z.center_longitude, "radius_m": z.radius_meters,
         "zone_type": z.zone_type}
        for z in rows
    ]


async def get_active_temporal_rules(session: AsyncSession) -> list[dict]:
    """Active temporal rules as plain dicts (the shape the pure engine takes)."""
    rows = (await session.execute(
        select(TemporalRule).where(TemporalRule.active)
    )).scalars().all()
    return [
        {"rule_name": r.rule_name, "parameters": r.parameters,
         "source_reference": r.source_reference, "rationale": r.rationale,
         "version": r.version}
        for r in rows
    ]


# ── Updates (version bump + audit, one transaction — Q4) ─────────────────────

def _audit(table: str, record_id: int, field: str, old, new,
           changed_by: str, reason: str) -> RuleAuditLog:
    return RuleAuditLog(
        table_name=table, record_id=record_id, field_changed=field,
        old_value=None if old is None else str(old),
        new_value=None if new is None else str(new),
        changed_by=changed_by, reason=reason,
    )


def _require_reason(changed_by: str, reason: str) -> None:
    if not changed_by or not reason or not reason.strip():
        raise RuleValidationError("changed_by and a non-empty reason are required")


async def update_weights(session: AsyncSession, new_weights: dict[str, float],
                         changed_by: str, reason: str) -> None:
    """Atomically re-version one or more weights; the RESULTING active set must
    sum to 1.0 ± WEIGHT_SUM_TOLERANCE or the whole update is rejected (Q2)."""
    _require_reason(changed_by, reason)
    unknown = set(new_weights) - KNOWN_FACTORS
    if unknown:
        raise RuleValidationError(f"Unknown factor(s): {sorted(unknown)}")
    for name, value in new_weights.items():
        if not 0.0 <= float(value) <= 1.0:
            raise RuleValidationError(f"Weight {name}={value} outside [0, 1]")

    current = await get_active_weights(session)
    resulting = {**current, **{k: float(v) for k, v in new_weights.items()}}
    total = sum(resulting.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise RuleValidationError(
            f"Rejected: resulting weights sum to {total:.4f}, not 1.0 "
            f"(±{WEIGHT_SUM_TOLERANCE}). The risk formula does not renormalize."
        )

    for name, value in new_weights.items():
        old_row = (await session.execute(
            select(RiskFactorWeight)
            .where(RiskFactorWeight.factor_name == name, RiskFactorWeight.active)
        )).scalar_one()
        old_row.active = False
        new_row = RiskFactorWeight(
            factor_name=name, weight=float(value), active=True,
            version=old_row.version + 1,
            source_reference=old_row.source_reference,
            rationale=old_row.rationale, created_by=changed_by,
        )
        session.add(new_row)
        await session.flush()  # assign new_row.id for the audit pointer
        session.add(_audit("risk_factor_weights", new_row.id, "weight",
                           old_row.weight, new_row.weight, changed_by, reason))


async def update_weight(session: AsyncSession, factor: str, new_value: float,
                        changed_by: str, reason: str) -> None:
    """Single-weight update — same strict sum rule as update_weights."""
    await update_weights(session, {factor: new_value}, changed_by, reason)


async def update_threshold(session: AsyncSession, name: str, new_value: float,
                           changed_by: str, reason: str,
                           source_reference: str | None = None,
                           rationale: str | None = None) -> None:
    """Re-version a threshold. ``source_reference``/``rationale`` default to the
    old row's values (a pure value change); pass them to update the citation
    too — the old (now-inactive) row keeps its original citation, so history is
    preserved."""
    _require_reason(changed_by, reason)
    if name not in KNOWN_THRESHOLDS:
        raise RuleValidationError(f"Unknown threshold {name!r}")
    if float(new_value) < 0.0:
        raise RuleValidationError(f"Threshold {name}={new_value} must be >= 0")

    old_row = (await session.execute(
        select(RiskThreshold)
        .where(RiskThreshold.threshold_name == name, RiskThreshold.active)
    )).scalar_one()
    old_row.active = False
    new_row = RiskThreshold(
        threshold_name=name, value=float(new_value), unit=old_row.unit,
        source_reference=source_reference if source_reference is not None
        else old_row.source_reference,
        rationale=rationale if rationale is not None else old_row.rationale,
        active=True, version=old_row.version + 1, created_by=changed_by,
    )
    session.add(new_row)
    await session.flush()
    session.add(_audit("risk_thresholds", new_row.id, "value",
                       old_row.value, new_row.value, changed_by, reason))


async def update_temporal_rule(session: AsyncSession, rule_name: str,
                               new_parameters: dict, changed_by: str,
                               reason: str) -> None:
    """Re-version a temporal rule's parameters (old active=False, new row
    version+1, audit) — atomic per Q4. Keeps exactly one active row per name."""
    _require_reason(changed_by, reason)
    if rule_name not in KNOWN_TEMPORAL_RULES:
        raise RuleValidationError(f"Unknown temporal rule {rule_name!r}")
    if not isinstance(new_parameters, dict) or not new_parameters:
        raise RuleValidationError("new_parameters must be a non-empty dict")

    old_row = (await session.execute(
        select(TemporalRule)
        .where(TemporalRule.rule_name == rule_name, TemporalRule.active)
    )).scalar_one()
    old_row.active = False
    new_row = TemporalRule(
        rule_name=rule_name, parameters=new_parameters, active=True,
        version=old_row.version + 1, source_reference=old_row.source_reference,
        rationale=old_row.rationale, created_by=changed_by,
    )
    session.add(new_row)
    await session.flush()
    session.add(_audit("temporal_rules", new_row.id, "parameters",
                       old_row.parameters, new_row.parameters, changed_by, reason))


async def add_danger_zone(session: AsyncSession, *, name: str, latitude: float,
                          longitude: float, radius_m: float, zone_type: str,
                          source_reference: str, rationale: str,
                          changed_by: str, reason: str) -> int:
    """Insert a new active danger zone; returns its id."""
    _require_reason(changed_by, reason)
    if zone_type not in ZONE_TYPES:
        raise RuleValidationError(
            f"zone_type {zone_type!r} not in {sorted(ZONE_TYPES)}")
    if radius_m <= 0:
        raise RuleValidationError("radius_m must be positive")

    zone = DangerZone(
        name=name, center_latitude=latitude, center_longitude=longitude,
        radius_meters=radius_m, zone_type=zone_type, active=True,
        source_reference=source_reference, rationale=rationale,
        created_by=changed_by,
    )
    session.add(zone)
    await session.flush()
    session.add(_audit("danger_zones", zone.id, "zone", None,
                       f"{name} ({latitude}, {longitude}) r={radius_m}m",
                       changed_by, reason))
    return zone.id


async def deactivate_danger_zone(session: AsyncSession, zone_id: int,
                                 changed_by: str, reason: str) -> None:
    _require_reason(changed_by, reason)
    zone = (await session.execute(
        select(DangerZone).where(DangerZone.id == zone_id, DangerZone.active)
    )).scalar_one_or_none()
    if zone is None:
        raise RuleValidationError(f"No active danger zone with id={zone_id}")
    zone.active = False
    session.add(_audit("danger_zones", zone.id, "active", True, False,
                       changed_by, reason))
