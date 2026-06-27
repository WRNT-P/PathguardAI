# pathguard/backend/app/ai/module3_risk/__init__.py
"""Module 3 — Risk Scoring: normalize factors, score 0–100, handle GPS loss, decide emergency."""
from .data_normalization import (
    normalize_route_deviation,
    scale_wandering,
    convert_boolean,
    compute_unfamiliarity,
)
from .risk_score_calculation import calculate_risk
from .gps_failure_handling import detect_gps_gap
from .emergency_decision_engine import decide_emergency
from .risk_data_collection import collect_risk_factors

__all__ = [
    "normalize_route_deviation",
    "scale_wandering",
    "convert_boolean",
    "compute_unfamiliarity",
    "calculate_risk",
    "detect_gps_gap",
    "decide_emergency",
    "collect_risk_factors",
]
