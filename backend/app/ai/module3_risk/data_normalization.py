# pathguard/backend/app/ai/module3_risk/data_normalization.py
"""Module 3.1 — Data Normalization.

Pure functions that map each raw risk signal onto the [0, 1] scale the risk
formula expects. No DB, no Module 2 imports, no model fitting — just arithmetic.

Every function clamps its result to [0.0, 1.0] so the weighted sum in
``risk_score_calculation`` can never exceed 100 or drop below 0. Inputs are
assumed to be real numbers: passing ``None`` raises (the caller owns validity).
"""
from __future__ import annotations


def _clamp01(value: float) -> float:
    """Clamp a value into the closed interval [0.0, 1.0]."""
    return min(1.0, max(0.0, value))


def normalize_route_deviation(meters: float, max_distance: float) -> float:
    """Distance off the predicted route, scaled so ``max_distance`` m -> 1.0 (D).

    ``max_distance`` comes from the rule KB (``route_deviation_ceiling_m``) —
    no hardcoded default, the caller must pass it.
    """
    return _clamp01(meters / max_distance)


def scale_wandering(score: float) -> float:
    """Wandering score (already 0–1 from Module 2), clamped for safety (W)."""
    return _clamp01(score)


def convert_boolean(flag: bool) -> float:
    """Boolean flag (e.g. danger zone) to 1.0 / 0.0 (Z)."""
    return 1.0 if flag else 0.0


def compute_unfamiliarity(familiarity: float) -> float:
    """Unfamiliarity = 1 - familiarity, clamped to [0, 1] (F)."""
    return _clamp01(1.0 - familiarity)
