from datetime import datetime
from typing import Literal, get_args
from pydantic import BaseModel

# The canonical set of alert types. Every ``crud.save_alert`` call in app/api
# must draw its ``alert_type`` from here, and tests/test_alert_types.py enforces
# that against the source. This list is the single place the set is written down:
# it was two places once, and "gps_loss" vs "gps_lost" cost a caregiver a
# duplicate push per GPS outage because the cooldown is keyed on the type.
AlertType = Literal[
    "wandering", "geofence", "gps_loss", "emergency", "sos", "trip_denied",
    "safe_zone_exit",
    # A press from the home screen, where the patient is not out walking.
    # Its own type, not a severity on "sos", because the push cooldown is
    # keyed on (patient, alert_type): sharing one would let a press from the
    # sofa silently swallow the push for a press made lost in the street.
    "sos_home",
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
    """⚠️ No route serves this. ``api/alerts.py``'s ``AlertOut`` is what the
    app actually receives — this is a second, older description of the same
    row that nothing renders.

    Adding a field here and believing it shipped cost a day: the value was
    written to the database, the schema said it existed, and the endpoint
    never returned it. Change ``AlertOut`` and ``_to_out``, then check the
    response body rather than the schema.
    """
    id: int
    patient_id: int
    alert_type: str
    severity: str
    message: str
    latitude: float | None
    longitude: float | None
    # The safe place the patient's own app is walking them to, when there is
    # one. Null on every alert type but a mid-journey SOS.
    destination_name: str | None = None
    resolved: bool
    resolved_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertResolve(BaseModel):
    resolved: bool = True
