# pathguard/backend/app/ai/module3_risk/emergency_decision_engine.py
"""Module 3.5 — Emergency Decision Engine.

Pure decision: given the already-computed risk score and the danger-zone flag,
decide whether a caregiver alert should fire and why. No DB, no Module 2, no
notification — the api/ layer acts on this decision (crud.save_alert + notify),
keeping ai/ side-effect-free like Module 5.

Rule (3.5): emergency fires when ``danger_zone`` OR ``risk_score >
emergency_score`` (strict ``>``). The trigger score is NOT hardcoded — it lives
in the rule KB (``risk_thresholds.emergency_score``, cited to the Alzheimer's
Association Safe Return guidance) and is passed in by the api/ layer. The two
trigger paths stay distinguishable via ``reason``, and ``danger_zone`` takes
precedence when both hold.

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


def decide_emergency(risk_score: float, danger_zone: bool,
                     emergency_score: float) -> dict:
    """Decide whether an emergency alert should fire, and on which trigger.

    ``emergency_score`` comes from the rule KB; the comparison is strict ``>``
    (a score exactly AT the threshold does not fire).
    """
    if danger_zone:
        return {
            "emergency": True,
            "reason": "danger_zone",
            "severity": "critical",
            "alert_type": "geofence",
        }
    if risk_score > emergency_score:
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
