from datetime import datetime
from pydantic import BaseModel, Field


class GPSDataCreate(BaseModel):
    patient_id: int
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy: float | None = None
    speed: float | None = None
    altitude: float | None = None
    recorded_at: datetime


class GPSDataResponse(BaseModel):
    id: int
    patient_id: int
    latitude: float
    longitude: float
    accuracy: float | None
    speed: float | None
    altitude: float | None
    smooth_latitude: float | None
    smooth_longitude: float | None
    recorded_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class LiveGPSUpdate(BaseModel):
    """Payload written to Firebase Realtime DB for live tracking."""
    patient_id: int
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy: float | None = None
    speed: float | None = None
    timestamp: datetime
