from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class UserCreate(BaseModel):
    firebase_uid: str
    name: str
    role: Literal["patient", "caregiver"]
    caregiver_id: int | None = None


class UserResponse(BaseModel):
    id: int
    firebase_uid: str
    name: str
    role: str
    caregiver_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BehavioralProfileResponse(BaseModel):
    id: int
    patient_id: int
    known_places: str | None         # JSON string
    routine_patterns: str | None     # JSON string
    typical_range_km: float | None
    last_trained_at: datetime | None
    updated_at: datetime

    model_config = {"from_attributes": True}
