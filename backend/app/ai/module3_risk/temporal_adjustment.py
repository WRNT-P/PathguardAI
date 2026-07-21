# pathguard/backend/app/ai/module3_risk/temporal_adjustment.py
"""Module 3.4 — Temporal Rules.

Pure logic: given the current round's score plus the patient's recent score
HISTORY, apply history-based rules that a single-reading score can't express.
No DB, no Module 2/4, no side effects — the caller (api/risk.py) loads the
active temporal rules from the KB and passes them in, same purity discipline as
the rest of ai/.

Two rules (values come from each rule's ``parameters`` — nothing hardcoded):

  trend_escalation (B1)
    IF the last ``window`` previous scores rise monotonically
    (score[t-3] < score[t-2] < score[t-1]) THEN add ``boost`` to the current
    score before level classification. Trajectory-based early warning (NEWS).

  sustained_high_risk (B2)
    IF the last ``window`` scores INCLUDING the current one are all
    >= ``min_score`` THEN force an emergency (reason handled by the caller).
    Sustained elevation → escalate (MOPH ED Triage Level 3).

Design notes:
  - Both conditions read the BASE current score (before B1's boost) so the two
    rules stay independent (design D4).
  - The function re-derives the level from the (possibly boosted) score using
    the passed-in ceilings, so it is the single source of truth for the final
    (score, level, emergency) — design D1.
  - Additive only: with too little history no rule can fire, so a patient with
    <window history is returned completely unchanged (cold-start parity).
"""
from __future__ import annotations

TREND_ESCALATION = "trend_escalation"
SUSTAINED_HIGH_RISK = "sustained_high_risk"


def _classify(score: float, low_ceiling: float, medium_ceiling: float) -> str:
    """Same mapping as risk_score_calculation._risk_level (kept in sync)."""
    if score < low_ceiling:
        return "low"
    if score < medium_ceiling:
        return "medium"
    return "high"


def _is_monotonic_increase(recent_scores: list[float], window: int) -> bool:
    """True if the newest ``window`` previous scores rose monotonically over
    time. ``recent_scores`` is newest-first, so rising-over-time means strictly
    decreasing as the index grows."""
    if window < 2 or len(recent_scores) < window:
        return False
    seq = recent_scores[:window]
    return all(seq[i] > seq[i + 1] for i in range(window - 1))


def apply_temporal_rules(
    current_score: float,
    current_level: str,
    current_emergency: bool,
    recent_scores: list[float],
    temporal_rules: list[dict],
    *,
    low_ceiling: float,
    medium_ceiling: float,
) -> tuple[float, str, bool, list[str]]:
    """Apply the active temporal rules to the current round.

    ``recent_scores``: this patient's previous scores, newest first, EXCLUDING
    the current one. Returns ``(adjusted_score, adjusted_level,
    adjusted_emergency, triggered_rule_names)``.
    """
    params = {r["rule_name"]: (r.get("parameters") or {}) for r in temporal_rules}
    triggered: list[str] = []
    adjusted_score = current_score
    adjusted_emergency = current_emergency

    # ── B1: escalating trend → boost the score ────────────────────────────────
    trend = params.get(TREND_ESCALATION)
    if trend is not None:
        window = int(trend.get("window", 3))
        boost = float(trend.get("boost", 10.0))
        if _is_monotonic_increase(recent_scores, window):
            adjusted_score = round(adjusted_score + boost, 1)
            triggered.append(TREND_ESCALATION)

    # ── B2: sustained high risk → force emergency (uses BASE current, D4) ─────
    sustained = params.get(SUSTAINED_HIGH_RISK)
    if sustained is not None:
        window = int(sustained.get("window", 5))
        min_score = float(sustained.get("min_score", 50.0))
        history_needed = window - 1  # current counts as one of the ``window``
        if (current_score >= min_score
                and len(recent_scores) >= history_needed
                and all(s >= min_score for s in recent_scores[:history_needed])):
            adjusted_emergency = True
            triggered.append(SUSTAINED_HIGH_RISK)

    # Level follows the boosted score; if the score is unchanged the caller's
    # classification stands (guarantees exact cold-start parity).
    if TREND_ESCALATION in triggered:
        adjusted_level = _classify(adjusted_score, low_ceiling, medium_ceiling)
    else:
        adjusted_level = current_level

    return adjusted_score, adjusted_level, adjusted_emergency, triggered
