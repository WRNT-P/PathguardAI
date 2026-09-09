"""Helpers for writing to Firebase Realtime DB.

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


def set_patient_access(patient_id: int, firebase_uids: list[str]) -> None:
    """Mirror who may see this patient into the Realtime Database.

    The rules there cannot read ``patient_caregivers`` out of Postgres, so
    without this copy "may this account open this chat" can only be answered
    as "is it signed in at all" — every caregiver in the system able to read
    every family's room.

    Written as the whole node rather than one key at a time, so a caregiver
    who was removed loses access on the next sync instead of keeping it until
    somebody remembers to delete their key.
    """
    ref = get_firebase_ref(f"access/{patient_id}")
    ref.set({uid: True for uid in firebase_uids})


async def sync_patient_access(db, patient_id: int) -> None:
    """Push the current access list for one patient. Never raises.

    Best-effort for the same reason the live position push is: Firebase being
    unreachable must not fail the request that created the link. The cost of a
    dropped sync is a caregiver who cannot open the chat until the next one,
    which is recoverable — a failed pairing is not.

    Re-runs are free, and are the fix for any sync that was dropped:
    ``scripts/sync_access_index.py`` calls this for every patient.
    """
    import logging

    from app.db import crud

    try:
        uids = await crud.get_patient_access_uids(db, patient_id)
        set_patient_access(patient_id, uids)
    except Exception:
        logging.getLogger(__name__).warning(
            "could not sync Realtime Database access for patient=%s — chat and "
            "trip requests stay closed to them until the next sync",
            patient_id, exc_info=True,
        )
