"""Module 3.4 — emergency decision engine (danger-zone OR score > threshold).

The emergency threshold is passed explicitly (80.0 mirrors the KB seed's
``emergency_score``). Includes the boundary-parity pin: exactly AT the
threshold does NOT fire (strict >), while the level mapping treats 80.0 as
"high" (>=) — two different operators on the same number, preserved from the
pre-refactor code.
"""
from app.ai.module3_risk.emergency_decision_engine import decide_emergency

EMERGENCY_SCORE = 80.0  # mirrors seed_risk_rules


def test_high_score_path():
    assert decide_emergency(85.0, False, EMERGENCY_SCORE) == {
        "emergency": True,
        "reason": "high_score",
        "severity": "high",
        "alert_type": "emergency",
    }


def test_danger_zone_fires_regardless_of_score():
    assert decide_emergency(10.0, True, EMERGENCY_SCORE) == {
        "emergency": True,
        "reason": "danger_zone",
        "severity": "critical",
        "alert_type": "geofence",
    }


def test_danger_zone_takes_precedence_over_high_score():
    r = decide_emergency(95.0, True, EMERGENCY_SCORE)
    assert r["reason"] == "danger_zone"
    assert r["alert_type"] == "geofence"
    assert r["severity"] == "critical"


def test_score_exactly_at_threshold_is_not_emergency():
    # strict > threshold
    assert decide_emergency(80.0, False, EMERGENCY_SCORE) == {
        "emergency": False,
        "reason": None,
        "severity": "normal",
        "alert_type": "none",
    }


def test_just_above_threshold_fires():
    """Boundary parity pin: 80.0 -> no emergency, 80.001 -> emergency."""
    assert decide_emergency(80.001, False, EMERGENCY_SCORE)["emergency"] is True


def test_low_score_no_emergency():
    r = decide_emergency(50.0, False, EMERGENCY_SCORE)
    assert r["emergency"] is False
    assert r["reason"] is None


def test_threshold_is_live_not_hardcoded():
    """Same score, lower KB threshold -> fires. Proves the rule is injected."""
    assert decide_emergency(75.0, False, 80.0)["emergency"] is False
    assert decide_emergency(75.0, False, 70.0)["emergency"] is True
