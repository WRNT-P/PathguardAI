from typing import Literal
from pydantic import BaseModel


class RecommendationFlags(BaseModel):
    time_match_available: bool  # False until Module 1 writes routine_patterns
    location_used: bool         # whether proximity contributed to scoring


class RecommendedPlace(BaseModel):
    rank: int
    cluster_id: int
    latitude: float
    longitude: float
    confidence: float       # 0-1
    confidence_pct: int     # 0-100 convenience
    factors: dict[str, float]  # {frequency, proximity, familiarity, time_match}


class RecommendationResponse(BaseModel):
    patient_id: int
    status: Literal["ok", "no_profile"]
    message: str
    flags: RecommendationFlags
    recommendations: list[RecommendedPlace]
