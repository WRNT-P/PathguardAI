"""Module 3.4 — emergency decision engine (pure rule)."""
from app.ai.module3_risk.emergency_decision_engine import decide_emergency


def test_high_score_path():
    assert decide_emergency(85.0, False) == {
        "emergency": True,
        "reason": "high_score",
        "severity": "high",
        "alert_type": "emergency",
    }


def test_danger_zone_fires_regardless_of_score():
    assert decide_emergency(10.0, True) == {
        "emergency": True,
        "reason": "danger_zone",
        "severity": "critical",
        "alert_type": "geofence",
    }


def test_danger_zone_takes_precedence_over_high_score():
    r = decide_emergency(95.0, True)
    assert r["reason"] == "danger_zone"
    assert r["alert_type"] == "geofence"
    assert r["severity"] == "critical"


def test_score_exactly_80_is_not_emergency():
    # strict > 80
    assert decide_emergency(80.0, False) == {
        "emergency": False,
        "reason": None,
        "severity": "normal",
        "alert_type": "none",
    }


def test_low_score_no_emergency():
    r = decide_emergency(50.0, False)
    assert r["emergency"] is False
    assert r["reason"] is None
