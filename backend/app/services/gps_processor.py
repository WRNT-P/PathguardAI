"""GPS ingestion orchestrator — the single writer of GPS data.

For each incoming reading it: (1) Kalman-smooths the coordinates, (2) persists
raw + smoothed history to PostgreSQL (the source of truth the AI modules read),
and (3) pushes the live position to Firebase for the caregiver map.

PostgreSQL is written first and the Firebase push is best-effort, so history is
never lost if Firebase is unavailable (e.g. during AI development without
Firebase credentials configured).
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.models import GPSData
from app.models.gps_data import GPSDataCreate, LiveGPSUpdate
from app.services import firebase, kalman_filter

logger = logging.getLogger(__name__)


async def process_gps_point(db: AsyncSession, raw: GPSDataCreate) -> GPSData:
    """Smooth, persist, and broadcast one GPS reading; return the stored row."""
    smooth_lat, smooth_lon = kalman_filter.smooth(
        raw.patient_id, raw.latitude, raw.longitude
    )

    # 1. Persist to PostgreSQL — source of truth for the AI modules.
    point = await crud.save_gps_point(
        db,
        patient_id=raw.patient_id,
        latitude=raw.latitude,
        longitude=raw.longitude,
        recorded_at=raw.recorded_at,
        accuracy=raw.accuracy,
        speed=raw.speed,
        altitude=raw.altitude,
        smooth_latitude=smooth_lat,
        smooth_longitude=smooth_lon,
    )

    # 2. Push the smoothed live position to Firebase — best-effort.
    try:
        firebase.update_live_position(
            LiveGPSUpdate(
                patient_id=raw.patient_id,
                latitude=smooth_lat,
                longitude=smooth_lon,
                accuracy=raw.accuracy,
                speed=raw.speed,
                timestamp=raw.recorded_at,
            )
        )
    except Exception as exc:  # noqa: BLE001 — never let live push block persistence
        logger.warning(
            "Firebase live update failed for patient %s: %s", raw.patient_id, exc
        )

    return point
