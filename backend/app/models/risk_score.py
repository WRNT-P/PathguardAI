from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class RiskScoreCreate(BaseModel):
    patient_id: int
    score: float = Field(..., ge=0, le=100)
    level: Literal["low", "medium", "high"]
    wandering_detected: bool = False
    gps_available: bool = True
    factors: str | None = None   # JSON string: {"distance_from_home": 0.8, ...}


class RiskScoreResponse(BaseModel):
    id: int
    patient_id: int
    score: float
    level: str
    wandering_detected: bool
    gps_available: bool
    factors: str | None
    calculated_at: datetime

    model_config = {"from_attributes": True}
