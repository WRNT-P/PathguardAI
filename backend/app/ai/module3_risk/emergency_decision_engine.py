# pathguard/backend/app/ai/module3_risk/emergency_decision_engine.py
"""Module 3.4 — Emergency Decision Engine.

Pure decision: given the already-computed risk score and the danger-zone flag,
decide whether a caregiver alert should fire and why. No DB, no Module 2, no
notification — the api/ layer acts on this decision (crud.save_alert + notify),
keeping ai/ side-effect-free like Module 5.

LOCKED rule (3.4): emergency fires when ``danger_zone`` OR ``risk_score > 80``
(strict ``>``). The two trigger paths stay distinguishable via ``reason``, and
``danger_zone`` takes precedence when both hold.

alert_type / severity choices:
  - danger_zone → severity "critical", alert_type "geofence"
    ("geofence" is one of the existing Alert.alert_type literals.)
  - high_score  → severity "high",     alert_type "emergency"
    ("emergency" is one of the documented Alert.alert_type literals
    [wandering|geofence|gps_loss|emergency].)
  - no emergency → severity "normal", alert_type "none". These non-firing
    defaults are never persisted (api/ only saves an alert when emergency=True).
"""
from __future__ import annotations


def decide_emergency(risk_score: float, danger_zone: bool) -> dict:
    """Decide whether an emergency alert should fire, and on which trigger."""
    if danger_zone:
        return {
            "emergency": True,
            "reason": "danger_zone",
            "severity": "critical",
            "alert_type": "geofence",
        }
    if risk_score > 80:
        return {
            "emergency": True,
            "reason": "high_score",
            "severity": "high",
            "alert_type": "emergency",
        }
    return {
        "emergency": False,
        "reason": None,
        "severity": "normal",
        "alert_type": "none",
    }


if __name__ == "__main__":
    # score > 80, no danger zone -> high_score path
    r = decide_emergency(85.0, False)
    assert r == {"emergency": True, "reason": "high_score",
                 "severity": "high", "alert_type": "emergency"}, r

    # danger zone with a low score -> danger_zone fires regardless of score
    r = decide_emergency(50.0, True)
    assert r == {"emergency": True, "reason": "danger_zone",
                 "severity": "critical", "alert_type": "geofence"}, r

    # both true -> danger_zone takes precedence as the reason
    r = decide_emergency(90.0, True)
    assert r["emergency"] is True and r["reason"] == "danger_zone", r
    assert r["alert_type"] == "geofence" and r["severity"] == "critical", r

    # exactly 80, no danger zone -> NOT an emergency (strict >)
    r = decide_emergency(80.0, False)
    assert r == {"emergency": False, "reason": None,
                 "severity": "normal", "alert_type": "none"}, r

    # low score, no danger zone -> no emergency, reason None
    r = decide_emergency(50.0, False)
    assert r["emergency"] is False and r["reason"] is None, r

    print("emergency_decision_engine: all assertions passed")
