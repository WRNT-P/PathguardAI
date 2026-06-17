# pathguard/backend/app/ai/module5_recommend/recommendation_prioritization.py
"""Module 5.3 — Recommendation Prioritization.

Ranks the scored places and keeps the top N. Sort by confidence, breaking ties
with visit_frequency so the more-established place wins an exact tie.
"""
from __future__ import annotations

from .recommendation_generation import ScoredPlace


def prioritize(scored: list[ScoredPlace], top_n: int = 3) -> list[ScoredPlace]:
    """Return the top ``top_n`` places, highest confidence first."""
    ranked = sorted(
        scored,
        key=lambda s: (s.confidence, s.visit_frequency),
        reverse=True,
    )
    return ranked[:top_n]
