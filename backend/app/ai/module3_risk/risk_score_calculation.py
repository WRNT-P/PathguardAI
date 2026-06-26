# pathguard/backend/app/ai/module3_risk/risk_score_calculation.py
"""Module 3.2 — Risk Score Calculation.

Pure logic: blends the five already-normalized factors (each 0–1, produced by
``data_normalization``) into a single 0–100 risk score with a transparent,
caregiver-readable breakdown. No DB, no Module 2, no model fitting.

LOCKED formula (weights sum to 1.0 — not renormalized):
    Risk = 0.30*route_deviation + 0.25*wandering + 0.20*confusion
         + 0.15*danger_zone     + 0.10*unfamiliarity

The caller (file 5) guarantees all five keys are present, so there is no
missing-key handling here. The emergency rule lives in file 4 — this file only
maps the score to a Low/Medium/High level.
"""
from __future__ import annotations

# Locked factor weights. Order defines the contributions dict order.
WEIGHTS = {
    "route_deviation": 0.30,
    "wandering": 0.25,
    "confusion": 0.20,
    "danger_zone": 0.15,
    "unfamiliarity": 0.10,
}


def _risk_level(score: float) -> str:
    """Map a 0–100 score to a lowercase level (matches DB + Module 5)."""
    if score < 50:
        return "low"
    if score < 80:
        return "medium"
    return "high"


def calculate_risk(factors: dict) -> dict:
    """Weighted-sum the five normalized factors into a score, level + breakdown.

    Each contribution is rounded to 1dp first, so the per-factor points always
    sum to ``risk_score`` (no drift between the breakdown and the headline).
    """
    contributions = {
        key: round(weight * factors[key] * 100, 1)
        for key, weight in WEIGHTS.items()
    }
    risk_score = round(sum(contributions.values()), 1)
    return {
        "risk_score": risk_score,
        "risk_level": _risk_level(risk_score),
        "contributions": contributions,
    }


if __name__ == "__main__":
    import math

    def eq(a: float, b: float) -> None:
        assert math.isclose(a, b, abs_tol=1e-9), f"{a} != {b}"

    KEYS = ["route_deviation", "wandering", "confusion", "danger_zone", "unfamiliarity"]

    def factors(*vals: float) -> dict:
        return dict(zip(KEYS, vals))

    # all-zero -> score 0.0, level "low", all contributions 0.0
    r = calculate_risk(factors(0.0, 0.0, 0.0, 0.0, 0.0))
    eq(r["risk_score"], 0.0)
    assert r["risk_level"] == "low", r["risk_level"]
    assert all(c == 0.0 for c in r["contributions"].values()), r["contributions"]

    # all-one -> score 100.0, level "high", contributions = [30,25,20,15,10]
    # (proves the weights sum to 1.0)
    r = calculate_risk(factors(1.0, 1.0, 1.0, 1.0, 1.0))
    eq(r["risk_score"], 100.0)
    assert r["risk_level"] == "high", r["risk_level"]
    eq(r["contributions"]["route_deviation"], 30.0)
    eq(r["contributions"]["wandering"], 25.0)
    eq(r["contributions"]["confusion"], 20.0)
    eq(r["contributions"]["danger_zone"], 15.0)
    eq(r["contributions"]["unfamiliarity"], 10.0)

    # mixed realistic case -> contributions sum equals risk_score
    r = calculate_risk(factors(0.64, 0.70, 0.40, 0.0, 0.70))
    eq(sum(r["contributions"].values()), r["risk_score"])
    eq(r["risk_score"], 51.7)
    assert r["risk_level"] == "medium", r["risk_level"]

    # level boundaries: all-0.5 -> exactly 50.0 -> "medium"; all-0.8 -> 80.0 -> "high"
    r = calculate_risk(factors(0.5, 0.5, 0.5, 0.5, 0.5))
    eq(r["risk_score"], 50.0)
    assert r["risk_level"] == "medium", r["risk_level"]

    r = calculate_risk(factors(0.8, 0.8, 0.8, 0.8, 0.8))
    eq(r["risk_score"], 80.0)
    assert r["risk_level"] == "high", r["risk_level"]

    print("risk_score_calculation: all assertions passed")
