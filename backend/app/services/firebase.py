"""Helpers for writing live tracking data to Firebase Realtime DB.

Firebase holds the *live* view (latest position per patient) for the caregiver
app; PostgreSQL holds the durable history. Firebase must be initialised once at
startup via ``app.db.database.init_firebase`` before these are called.
"""
from app.db.database import get_firebase_ref
from app.models.gps_data import LiveGPSUpdate


def update_live_position(update: LiveGPSUpdate) -> None:
    """Overwrite the patient's current position for real-time tracking."""
    ref = get_firebase_ref(f"live_positions/{update.patient_id}")
    ref.set(
        {
            "latitude": update.latitude,
            "longitude": update.longitude,
            "accuracy": update.accuracy,
            "speed": update.speed,
            "timestamp": update.timestamp.isoformat(),
        }
    )
