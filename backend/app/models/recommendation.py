from typing import Literal
from pydantic import BaseModel


class RecommendationFlags(BaseModel):
    # True when the patient has a routine on file *and* the request had a clock
    # to look it up against. Hardcoded False until 2026-08-27, which had stopped
    # being true on 2026-08-26 when routine_patterns gained a writer and
    # time_match gained weight 0.25.
    time_match_available: bool
    location_used: bool         # whether proximity contributed to scoring


class RecommendedPlace(BaseModel):
    rank: int
    cluster_id: int
    # None for a place Module 1 learned — clustering produces no name. In
    # practice every place is a caregiver pin, so in practice this is set.
    place_name: str | None = None
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
