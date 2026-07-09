# pathguard/backend/app/mock/seed_risk_rules.py
"""Seed the Module 3 rule knowledge base with the current (pre-refactor) values.

Every weight, threshold and danger zone that used to be hardcoded in
``app/ai/module3_risk/`` lives here with its medical source and rationale, so
system behavior is IDENTICAL after the expert-system refactor. Idempotent:
tables that already contain active rows are left untouched.

The dictionaries below are also imported by tests (``tests/conftest.py`` seeds
them into the in-memory test DB) so tests and the live demo share one source
of truth.

Run from the backend/ directory:
    python -m app.mock.seed_risk_rules
"""
import asyncio

from sqlalchemy import select

from app.db.models import (
    DangerZone, RiskFactorWeight, RiskThreshold, TemporalRule,
)

SEED_CREATED_BY = "seed_risk_rules"

# Weights sum to exactly 1.0 — calculate_risk does NOT renormalize (locked formula).
SEED_WEIGHTS = [
    {"factor_name": "route_deviation", "weight": 0.30,
     "source_reference": "TH-DMS-2564 §BPSD + TH-RAMA-BPSD",
     "rationale": ("Spatial disorientation is a core wandering feature, present in "
                   "~60% of dementia patients per Alzheimer's Association. Highest "
                   "weight due to strongest correlation with unsafe outcomes.")},
    {"factor_name": "wandering", "weight": 0.25,
     "source_reference": "TH-SIRIRAJ nursing manual 2562",
     "rationale": ("Aberrant motor behavior documented in >90% of dementia patients "
                   "with BPSD (Ramathibodi Hospital reference).")},
    {"factor_name": "confusion", "weight": 0.20,
     "source_reference": "TH-DMS-2564 §BPSD",
     "rationale": ("Confusion/disorientation is a diagnostic criterion for Mild "
                   "Dementia per Thai DMS 2564 guideline.")},
    {"factor_name": "danger_zone", "weight": 0.15,
     "source_reference": "Alzheimer's Assoc Safe Return Guide",
     "rationale": ("Environmental risk multiplier — patients near roads/waterways "
                   "face ~60% higher injury rate.")},
    {"factor_name": "unfamiliarity", "weight": 0.10,
     "source_reference": "TH-DMS-2564",
     "rationale": ("Context factor rather than direct symptom — patients may leave "
                   "familiar area during early-stage wandering.")},
]

SEED_THRESHOLDS = [
    {"threshold_name": "low_ceiling", "value": 50.0, "unit": "score",
     "source_reference": "MOPH ED Triage 2561 + ESI (Wuerz & Eitel 1998)",
     "rationale": ("MOPH ED Triage (กรมการแพทย์ กระทรวงสาธารณสุข พิมพ์ครั้งที่ 2, "
                   "2561) 5-level classification — score below 50 corresponds to "
                   "Level 4-5 (Less urgent / Non-urgent) requiring monitoring only, "
                   "no emergency intervention. Applied via Thailand National Triage "
                   "Guideline adopted from ESI v4. 75.8% of Thai hospitals use this "
                   "ESI-based algorithm (Soontorn et al., National Survey of Thailand "
                   "Emergency Departments).")},
    {"threshold_name": "medium_ceiling", "value": 80.0, "unit": "score",
     "source_reference": "MOPH ED Triage 2561 + Alzheimer's Assoc Safe Return",
     "rationale": ("MOPH ED Triage classifies scores >=80 as Level 1-2 "
                   "(Resuscitation/Emergent) requiring intervention within 0-10 "
                   "minutes. Consistent with Alzheimer's Association Safe Return "
                   "guidance that 'first few hours are critical' for missing "
                   "dementia patients.")},
    {"threshold_name": "emergency_score", "value": 80.0, "unit": "score",
     "source_reference": "MOPH ED Triage 2561 Level 1-2 + Alzheimer's Assoc",
     "rationale": ("Automatic alert threshold aligned with MOPH ED Triage "
                   "Level 1-2 requiring immediate intervention (0-10 min response "
                   "time). Alzheimer's Association reports 50% of missing dementia "
                   "patients suffer death or severe injury if not found within 24 "
                   "hours, justifying the same acuity level for wandering "
                   "emergencies.")},
    {"threshold_name": "route_deviation_ceiling_m", "value": 500.0, "unit": "meter",
     "source_reference": "Alzheimer's Assoc",
     "rationale": ("94% of missing dementia patients found within 1.5 mi (~2400 m); "
                   "500 m is a conservative early-warning boundary. TODO: review "
                   "with medical advisor whether to raise toward the evidence base.")},
    {"threshold_name": "gps_gap_seconds", "value": 600.0, "unit": "second",
     "source_reference": "Ali et al. wandering injury study",
     "rationale": ("10-min gap aligns with the initial critical window before "
                   "injury risk increases sharply.")},
]

# Temporal rules use a patient's score HISTORY. Tunables live in `parameters`
# so nothing is hardcoded in the pure engine (design D2). The min_score for
# sustained risk is stored here (D3) — it currently equals low_ceiling (50) but
# is independently tunable and carries its own citation.
SEED_TEMPORAL_RULES = [
    {"rule_name": "trend_escalation",
     "parameters": {"window": 3, "boost": 10.0},
     "source_reference": "NEWS (RCP National Early Warning Score, 2012/2017)",
     "rationale": ("Trend-based early warning: three consecutive rising risk "
                   "scores (score[t-3] < score[t-2] < score[t-1]) signal "
                   "deterioration before any single value is alarming, mirroring "
                   "the NHS National Early Warning Score principle that the "
                   "trajectory matters more than a lone reading. Adds +10 to the "
                   "current score before level classification.")},
    {"rule_name": "sustained_high_risk",
     "parameters": {"window": 5, "min_score": 50.0},
     "source_reference": "MOPH ED Triage 2561 Level 3 (Urgent, 30-min)",
     "rationale": ("Five consecutive scores at medium level or above (>=50) at "
                   "~2-min sampling ≈ 10 minutes of sustained risk. MOPH ED Triage "
                   "Level 3 (Urgent) mandates response within 30 minutes; sustained "
                   "elevation warrants escalating to a Level 1-2 emergency rather "
                   "than waiting for a single spike. Forces emergency=true, "
                   "reason 'sustained_risk', severity 'high'.")},
]

# Exactly the two demo circles previously hardcoded in risk_data_collection.py,
# so danger-zone behavior is byte-identical after the refactor.
SEED_DANGER_ZONES = [
    {"name": "Major highway interchange (demo)",
     "center_latitude": 13.7700, "center_longitude": 100.5550,
     "radius_meters": 150.0, "zone_type": "highway",
     "source_reference": "Alzheimer's Assoc Safe Return Guide",
     "rationale": ("Patients near roads/waterways face ~60% higher injury rate; "
                   "highway interchanges combine traffic exposure with poor "
                   "pedestrian visibility.")},
    {"name": "Canal / waterway edge (demo)",
     "center_latitude": 13.7400, "center_longitude": 100.5200,
     "radius_meters": 200.0, "zone_type": "waterway",
     "source_reference": "Alzheimer's Assoc Safe Return Guide",
     "rationale": ("Drowning is a leading cause of death among missing dementia "
                   "patients; unfenced waterway edges are high-lethality zones.")},
]


async def seed_rules(session) -> dict:
    """Insert any missing KB rows (weights/thresholds/zones/temporal). Caller commits."""
    counts = {"weights": 0, "thresholds": 0, "danger_zones": 0, "temporal_rules": 0}

    existing = set(
        (await session.execute(
            select(RiskFactorWeight.factor_name).where(RiskFactorWeight.active))
         ).scalars())
    for w in SEED_WEIGHTS:
        if w["factor_name"] not in existing:
            session.add(RiskFactorWeight(**w, active=True, version=1,
                                         created_by=SEED_CREATED_BY))
            counts["weights"] += 1

    existing = set(
        (await session.execute(
            select(RiskThreshold.threshold_name).where(RiskThreshold.active))
         ).scalars())
    for t in SEED_THRESHOLDS:
        if t["threshold_name"] not in existing:
            session.add(RiskThreshold(**t, active=True, version=1,
                                      created_by=SEED_CREATED_BY))
            counts["thresholds"] += 1

    existing = set(
        (await session.execute(
            select(DangerZone.name).where(DangerZone.active))
         ).scalars())
    for z in SEED_DANGER_ZONES:
        if z["name"] not in existing:
            session.add(DangerZone(**z, active=True, created_by=SEED_CREATED_BY))
            counts["danger_zones"] += 1

    existing = set(
        (await session.execute(
            select(TemporalRule.rule_name).where(TemporalRule.active))
         ).scalars())
    for tr in SEED_TEMPORAL_RULES:
        if tr["rule_name"] not in existing:
            session.add(TemporalRule(**tr, active=True, version=1,
                                     created_by=SEED_CREATED_BY))
            counts["temporal_rules"] += 1

    await session.flush()
    return counts


async def main() -> None:
    from app.db.database import AsyncSessionLocal, init_db

    await init_db()
    async with AsyncSessionLocal() as session:
        counts = await seed_rules(session)
        await session.commit()
    print(f"Seeded: {counts}  (0 = already present, seed is idempotent)")
    print("Inspect:  curl http://localhost:8000/api/admin/rules")


if __name__ == "__main__":
    asyncio.run(main())
