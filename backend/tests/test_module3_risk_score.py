"""Module 3.2 — risk score calculation (weighted sum, level mapping)."""
import math

import pytest

from app.ai.module3_risk.risk_score_calculation import WEIGHTS, calculate_risk

KEYS = ["route_deviation", "wandering", "confusion", "danger_zone", "unfamiliarity"]


def factors(*vals):
    return dict(zip(KEYS, vals))


def test_weights_sum_to_one():
    assert math.isclose(sum(WEIGHTS.values()), 1.0, abs_tol=1e-9)


def test_all_zero_is_low():
    r = calculate_risk(factors(0, 0, 0, 0, 0))
    assert r["risk_score"] == 0.0
    assert r["risk_level"] == "low"
    assert all(c == 0.0 for c in r["contributions"].values())


def test_all_one_is_high_with_weighted_contributions():
    r = calculate_risk(factors(1, 1, 1, 1, 1))
    assert r["risk_score"] == 100.0
    assert r["risk_level"] == "high"
    assert r["contributions"] == {
        "route_deviation": 30.0,
        "wandering": 25.0,
        "confusion": 20.0,
        "danger_zone": 15.0,
        "unfamiliarity": 10.0,
    }


def test_contributions_sum_to_headline_score():
    r = calculate_risk(factors(0.64, 0.70, 0.40, 0.0, 0.70))
    assert math.isclose(sum(r["contributions"].values()), r["risk_score"], abs_tol=1e-9)
    assert r["risk_score"] == 51.7
    assert r["risk_level"] == "medium"


@pytest.mark.parametrize(
    "value, expected_score, expected_level",
    [
        (0.5, 50.0, "medium"),   # exactly 50 -> medium (boundary)
        (0.8, 80.0, "high"),     # exactly 80 -> high (boundary)
        (0.49, 48.9, "low"),   # per-factor rounding -> 48.9, not 49.0
    ],
)
def test_level_boundaries(value, expected_score, expected_level):
    r = calculate_risk(factors(value, value, value, value, value))
    assert r["risk_score"] == expected_score
    assert r["risk_level"] == expected_level
