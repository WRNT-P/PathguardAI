# pathguard/backend/app/ai/module5_recommend/__init__.py
"""Module 5 — Smart Recommendation: top likely places with confidence scores."""
from .user_context_analysis import UserContext, build_user_context
from .recommendation_generation import ScoredPlace, generate_recommendations, score_place
from .recommendation_prioritization import prioritize

__all__ = [
    "UserContext",
    "build_user_context",
    "ScoredPlace",
    "score_place",
    "generate_recommendations",
    "prioritize",
]

# The learned ranker (ranker.Module5Ranker / ranker.load_ranker) is imported
# directly from its module to keep this package import light — pulling it here
# would drag sklearn into every Module 5 import.
