"""Module 3.3 — risk score calculation (weighted sum, level mapping).

Pure-function tests: weights and level boundaries are passed explicitly (the
values below mirror the KB seed — tests hardcode EXPECTATIONS, the app does
not hardcode rules). That the seeded KB itself sums to 1.0 is asserted in
test_rule_repository.py.
"""
import math

import pytest

from app.ai.module3_risk.risk_score_calculation import calculate_risk

KEYS = ["route_deviation", "wandering", "confusion", "danger_zone", "unfamiliarity"]

# Test-local rule values (mirror app/mock/seed_risk_rules.py).
WEIGHTS = {
    "route_deviation": 0.30,
    "wandering": 0.25,
    "confusion": 0.20,
    "danger_zone": 0.15,
    "unfamiliarity": 0.10,
}
LOW_CEILING = 50.0
MEDIUM_CEILING = 80.0


def factors(*vals):
    return dict(zip(KEYS, vals))


def score(f):
    return calculate_risk(f, WEIGHTS, low_ceiling=LOW_CEILING,
                          medium_ceiling=MEDIUM_CEILING)


def test_all_zero_is_low():
    r = score(factors(0, 0, 0, 0, 0))
    assert r["risk_score"] == 0.0
    assert r["risk_level"] == "low"
    assert all(c == 0.0 for c in r["contributions"].values())


def test_all_one_is_high_with_weighted_contributions():
    r = score(factors(1, 1, 1, 1, 1))
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
    r = score(factors(0.64, 0.70, 0.40, 0.0, 0.70))
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
    r = score(factors(value, value, value, value, value))
    assert r["risk_score"] == expected_score
    assert r["risk_level"] == expected_level


def test_different_weights_change_the_score():
    """The KB is live: pass different weights, get a different score."""
    flat = {k: 0.2 for k in KEYS}
    f = factors(1.0, 0.0, 0.0, 0.0, 0.0)
    assert score(f)["risk_score"] == 30.0
    r = calculate_risk(f, flat, low_ceiling=50.0, medium_ceiling=80.0)
    assert r["risk_score"] == 20.0
