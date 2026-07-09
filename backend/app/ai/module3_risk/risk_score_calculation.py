# pathguard/backend/app/ai/module3_risk/risk_score_calculation.py
"""Module 3.2 — Risk Score Calculation.

Pure logic: blends the five already-normalized factors (each 0–1, produced by
``data_normalization``) into a single 0–100 risk score with a transparent,
caregiver-readable breakdown. No DB, no Module 2, no model fitting.

Formula (weights sum to 1.0 — not renormalized):
    Risk = Σ weight[factor] * factors[factor] * 100

Weights and level boundaries are NOT hardcoded here — they live in the rule
knowledge base (``risk_factor_weights`` / ``risk_thresholds`` tables, seeded by
``app/mock/seed_risk_rules.py`` with medical sources) and are passed in by the
caller (``app/api/risk.py`` loads them via ``app.db.rule_repository``). This
file stays pure so it is unit-testable without a DB.

The caller guarantees all five factor keys are present and that the weights
sum to 1.0 (the repository rejects any update that would break that invariant).
The emergency rule lives in ``emergency_decision_engine`` — this file only maps
the score to a Low/Medium/High level.
"""
from __future__ import annotations


def _risk_level(score: float, low_ceiling: float, medium_ceiling: float) -> str:
    """Map a 0–100 score to a lowercase level (matches DB + Module 5).

    ``score < low_ceiling`` -> "low"; ``score < medium_ceiling`` -> "medium";
    else "high" (so exactly at a ceiling bumps to the next level).
    """
    if score < low_ceiling:
        return "low"
    if score < medium_ceiling:
        return "medium"
    return "high"


def calculate_risk(factors: dict, weights: dict, *,
                   low_ceiling: float, medium_ceiling: float) -> dict:
    """Weighted-sum the five normalized factors into a score, level + breakdown.

    ``weights`` is {factor_name: weight 0–1} from the rule KB;
    ``low_ceiling``/``medium_ceiling`` are the level boundaries (0–100 scores).
    Each contribution is rounded to 1dp first, so the per-factor points always
    sum to ``risk_score`` (no drift between the breakdown and the headline).
    """
    contributions = {
        key: round(weight * factors[key] * 100, 1)
        for key, weight in weights.items()
    }
    risk_score = round(sum(contributions.values()), 1)
    return {
        "risk_score": risk_score,
        "risk_level": _risk_level(risk_score, low_ceiling, medium_ceiling),
        "contributions": contributions,
    }
