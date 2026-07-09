"""Module 3.5 — temporal adjustment (pure logic, no DB/ML).

Rule values are passed explicitly (mirroring the KB seed) — tests hardcode
EXPECTATIONS, the engine hardcodes nothing.
"""
import pytest

from app.ai.module3_risk.temporal_adjustment import apply_temporal_rules

# Mirror app/mock/seed_risk_rules.py SEED_TEMPORAL_RULES.
RULES = [
    {"rule_name": "trend_escalation", "parameters": {"window": 3, "boost": 10.0}},
    {"rule_name": "sustained_high_risk", "parameters": {"window": 5, "min_score": 50.0}},
]
LOW, MED = 50.0, 80.0


def apply(current_score, recent, *, level=None, emergency=False, rules=RULES):
    if level is None:
        level = "low" if current_score < LOW else "medium" if current_score < MED else "high"
    return apply_temporal_rules(current_score, level, emergency, recent, rules,
                                low_ceiling=LOW, medium_ceiling=MED)


# ── B1: escalating trend ──────────────────────────────────────────────────────

def test_monotonic_increase_adds_boost():
    # previous newest-first [30,20,10] => over time 10<20<30 (rising) => +10
    score, level, emergency, fired = apply(40.0, [30.0, 20.0, 10.0])
    assert score == 50.0
    assert "trend_escalation" in fired
    assert level == "medium"          # 40 (low) boosted to 50 (medium)
    assert emergency is False


def test_non_monotonic_does_not_trigger():
    score, level, emergency, fired = apply(40.0, [30.0, 35.0, 10.0])  # 10<35>30
    assert score == 40.0
    assert fired == []
    assert level == "low"


def test_equal_consecutive_is_not_strict_increase():
    score, _, _, fired = apply(40.0, [30.0, 30.0, 10.0])  # 30 == 30, not strict
    assert score == 40.0
    assert "trend_escalation" not in fired


def test_only_latest_window_matters():
    # window=3: newest 3 are [40,30,20] rising; the older 5.0 is ignored
    score, _, _, fired = apply(10.0, [40.0, 30.0, 20.0, 5.0])
    assert "trend_escalation" in fired
    assert score == 20.0


# ── B2: sustained high risk ───────────────────────────────────────────────────

def test_sustained_medium_forces_emergency():
    # current 55 + 4 previous all >=50 => 5 sustained => emergency
    score, level, emergency, fired = apply(55.0, [52.0, 60.0, 50.0, 51.0])
    assert emergency is True
    assert "sustained_high_risk" in fired
    assert score == 55.0              # B2 does not change the score
    assert level == "medium"


def test_sustained_needs_current_above_min():
    # current 45 (<50) breaks the sustained chain even if history qualifies
    score, level, emergency, fired = apply(45.0, [55.0, 60.0, 52.0, 51.0])
    assert emergency is False
    assert "sustained_high_risk" not in fired


def test_sustained_needs_full_window_of_history():
    # only 3 previous (need 4 for window=5) => cannot fire
    _, _, emergency, fired = apply(55.0, [55.0, 60.0, 52.0])
    assert emergency is False
    assert "sustained_high_risk" not in fired


def test_one_low_previous_breaks_sustained():
    _, _, emergency, fired = apply(55.0, [55.0, 49.0, 52.0, 51.0])  # 49 < 50
    assert emergency is False


# ── interaction / design D4 (B2 uses BASE current, not boosted) ───────────────

def test_b2_uses_base_current_not_boosted():
    # previous [52,51,50,60]: newest 3 (52,51,50) rise => B1 fires, 45 -> 55.
    # B2 must judge the BASE current (45 < 50) and NOT fire despite boosted 55.
    score, level, emergency, fired = apply(45.0, [52.0, 51.0, 50.0, 60.0])
    assert score == 55.0
    assert "trend_escalation" in fired
    assert "sustained_high_risk" not in fired
    assert emergency is False
    assert level == "medium"          # 45 (low) -> 55 (medium)


def test_both_rules_can_fire_together():
    # current 60; previous [58,55,52,51] newest3 rise (52<55<58) => B1;
    # current+4 all >=50 => B2.
    score, level, emergency, fired = apply(60.0, [58.0, 55.0, 52.0, 51.0])
    assert score == 70.0
    assert set(fired) == {"trend_escalation", "sustained_high_risk"}
    assert emergency is True
    assert level == "medium"


# ── cold start / parity ───────────────────────────────────────────────────────

def test_no_history_is_unchanged():
    score, level, emergency, fired = apply(72.5, [], level="medium", emergency=True)
    assert (score, level, emergency, fired) == (72.5, "medium", True, [])


def test_insufficient_history_is_unchanged():
    score, level, emergency, fired = apply(30.0, [20.0, 10.0])  # <3 for B1, <4 for B2
    assert (score, level, emergency, fired) == (30.0, "low", False, [])


def test_empty_rules_list_is_noop():
    score, level, emergency, fired = apply(60.0, [58.0, 55.0, 52.0, 51.0], rules=[])
    assert (score, level, emergency, fired) == (60.0, "medium", False, [])
