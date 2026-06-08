from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class AlertCreate(BaseModel):
    patient_id: int
    alert_type: Literal["wandering", "geofence", "gps_loss", "emergency"]
    severity: Literal["low", "medium", "high", "critical"]
    message: str
    latitude: float | None = None
    longitude: float | None = None


class AlertResponse(BaseModel):
    id: int
    patient_id: int
    alert_type: str
    severity: str
    message: str
    latitude: float | None
    longitude: float | None
    resolved: bool
    resolved_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertResolve(BaseModel):
    resolved: bool = True
