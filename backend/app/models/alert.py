from datetime import datetime
from typing import Literal, get_args
from pydantic import BaseModel

# The canonical set of alert types. Every ``crud.save_alert`` call in app/api
# must draw its ``alert_type`` from here, and tests/test_alert_types.py enforces
# that against the source. This list is the single place the set is written down:
# it was two places once, and "gps_loss" vs "gps_lost" cost a caregiver a
# duplicate push per GPS outage because the cooldown is keyed on the type.
AlertType = Literal[
    "wandering", "geofence", "gps_loss", "emergency", "sos", "trip_denied"
]
ALERT_TYPES: tuple[str, ...] = get_args(AlertType)


class AlertCreate(BaseModel):
    patient_id: int
    alert_type: AlertType
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
