"""CRUD / repository helpers — the data-access layer the AI modules build on.

AI modules (behavior, prediction, risk, …) should call these functions instead
of writing their own SQL, so the PostgreSQL schema stays owned in one place.

Transaction policy: these helpers ``flush`` but never ``commit``. The caller
owns the transaction — under FastAPI that is the ``get_db`` dependency, which
commits at the end of the request (see ``app/db/database.py``).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Alert, BehavioralProfile, GPSData, RiskScore


# ── GPS history ─────────────────────────────────────────────────────────────

async def save_gps_point(
    db: AsyncSession,
    patient_id: int,
    latitude: float,
    longitude: float,
    recorded_at: datetime,
    accuracy: float | None = None,
    speed: float | None = None,
    altitude: float | None = None,
    smooth_latitude: float | None = None,
    smooth_longitude: float | None = None,
) -> GPSData:
    """Persist one raw+smoothed GPS reading. Called by the gps_processor."""
    point = GPSData(
        patient_id=patient_id,
        latitude=latitude,
        longitude=longitude,
        accuracy=accuracy,
        speed=speed,
        altitude=altitude,
        smooth_latitude=smooth_latitude,
        smooth_longitude=smooth_longitude,
        recorded_at=recorded_at,
    )
    db.add(point)
    await db.flush()  # assign PK without committing — caller owns the tx
    return point


async def get_gps_history(
    db: AsyncSession, patient_id: int, days: int = 30,
) -> list[GPSData]:
    """Return a patient's GPS readings over the last ``days``, oldest first.

    The primary read for AI module 1 (behavior clustering / routine learning).
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(GPSData)
        .where(GPSData.patient_id == patient_id, GPSData.recorded_at >= since)
        .order_by(GPSData.recorded_at)
    )
    return list(result.scalars().all())


async def get_latest_gps(db: AsyncSession, patient_id: int) -> GPSData | None:
    """Return the most recent GPS reading, or None if the patient has none."""
    result = await db.execute(
        select(GPSData)
        .where(GPSData.patient_id == patient_id)
        .order_by(desc(GPSData.recorded_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Risk scores ─────────────────────────────────────────────────────────────

async def save_risk_score(
    db: AsyncSession,
    patient_id: int,
    score: float,
    level: str,
    wandering_detected: bool = False,
    gps_available: bool = True,
    factors: str | None = None,
) -> RiskScore:
    """Persist a risk score. Written by AI module 3 (risk)."""
    risk = RiskScore(
        patient_id=patient_id,
        score=score,
        level=level,
        wandering_detected=wandering_detected,
        gps_available=gps_available,
        factors=factors,
    )
    db.add(risk)
    await db.flush()
    return risk


async def get_latest_risk_score(db: AsyncSession, patient_id: int) -> RiskScore | None:
    """Return the most recent risk score, or None."""
    result = await db.execute(
        select(RiskScore)
        .where(RiskScore.patient_id == patient_id)
        .order_by(desc(RiskScore.calculated_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Alerts ──────────────────────────────────────────────────────────────────

async def save_alert(
    db: AsyncSession,
    patient_id: int,
    alert_type: str,
    severity: str,
    message: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> Alert:
    """Persist an alert. Written by AI module 3 (emergency decision engine)."""
    alert = Alert(
        patient_id=patient_id,
        alert_type=alert_type,
        severity=severity,
        message=message,
        latitude=latitude,
        longitude=longitude,
    )
    db.add(alert)
    await db.flush()
    return alert


# ── Behavioral profile ──────────────────────────────────────────────────────

async def get_behavioral_profile(
    db: AsyncSession, patient_id: int,
) -> BehavioralProfile | None:
    """Return a patient's learned profile (known places, routines), or None.

    Read by AI modules 2–5; written by module 1 via ``upsert_behavioral_profile``.
    """
    result = await db.execute(
        select(BehavioralProfile).where(BehavioralProfile.patient_id == patient_id)
    )
    return result.scalar_one_or_none()


async def upsert_behavioral_profile(
    db: AsyncSession,
    patient_id: int,
    known_places: str | None = None,
    routine_patterns: str | None = None,
    typical_range_km: float | None = None,
    last_trained_at: datetime | None = None,
) -> BehavioralProfile:
    """Create or update a patient's behavioral profile (one row per patient).

    Only non-None arguments overwrite existing values, so module 1 can update
    places and routines independently. Written by AI module 1 (behavior).
    """
    profile = await get_behavioral_profile(db, patient_id)
    if profile is None:
        profile = BehavioralProfile(patient_id=patient_id)
        db.add(profile)

    if known_places is not None:
        profile.known_places = known_places
    if routine_patterns is not None:
        profile.routine_patterns = routine_patterns
    if typical_range_km is not None:
        profile.typical_range_km = typical_range_km
    if last_trained_at is not None:
        profile.last_trained_at = last_trained_at

    await db.flush()
    return profile
