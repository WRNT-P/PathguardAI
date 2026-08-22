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

from app.db.models import (
    Alert, BehavioralProfile, DeviceToken, GPSData, PushNotification,
    RiskScore, User,
)


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_user_id_by_firebase_uid(
    db: AsyncSession, firebase_uid: str,
) -> int | None:
    """Resolve a Firebase/Flutter string UID to the internal int ``users.id``.

    The app calls ``/api/register`` once after sign-in and keeps the int id it
    gets back; every later request (GPS, risk, alerts) carries that int, because
    ``gps_data.patient_id`` is an int FK to ``users.id``. Returns None if no user
    is registered for that UID.
    """
    result = await db.execute(
        select(User.id).where(User.firebase_uid == firebase_uid)
    )
    return result.scalar_one_or_none()


async def user_exists(db: AsyncSession, user_id: int) -> bool:
    """True if ``users.id`` exists — lets callers reject a bad FK with a 404."""
    result = await db.execute(select(User.id).where(User.id == user_id))
    return result.scalar_one_or_none() is not None


async def create_user(
    db: AsyncSession,
    firebase_uid: str,
    name: str,
    role: str,
    caregiver_id: int | None = None,
) -> User:
    """Create a user row (the FK target GPS/risk/alert data references).

    Written by the register endpoint so a ``users.id`` exists before any GPS for
    that patient arrives. Caller should check ``get_user_id_by_firebase_uid``
    first to keep ``firebase_uid`` unique.
    """
    user = User(
        firebase_uid=firebase_uid,
        name=name,
        role=role,
        caregiver_id=caregiver_id,
    )
    db.add(user)
    await db.flush()
    return user


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
    direction: float | None = None,
    device_motion: str | None = None,
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
        direction=direction,
        device_motion=device_motion,
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


async def get_recent_risk_scores(
    db: AsyncSession, patient_id: int, limit: int = 5
) -> list[RiskScore]:
    """Return the patient's most recent risk scores, newest first.

    Used by Module 3's temporal rules (trend / sustained-risk) to look back at
    history. The current round has not been persisted yet when this is called,
    so the result is exactly the *previous* rounds.
    """
    result = await db.execute(
        select(RiskScore)
        .where(RiskScore.patient_id == patient_id)
        .order_by(desc(RiskScore.calculated_at))
        .limit(limit)
    )
    return list(result.scalars().all())


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


# ── Push notification ─────────────────────────────────────────────────────────

async def upsert_device_token(
    db: AsyncSession, user_id: int, token: str, platform: str,
) -> DeviceToken:
    """Register a caregiver device, or re-point an existing token at this user.

    The app re-POSTs its token on every launch, and Firebase hands the *same*
    token to a different account when a phone is shared, so the token — not the
    user — is the identity here.
    """
    result = await db.execute(select(DeviceToken).where(DeviceToken.token == token))
    row = result.scalar_one_or_none()

    if row is None:
        row = DeviceToken(user_id=user_id, token=token, platform=platform)
        db.add(row)
    else:
        row.user_id = user_id
        row.platform = platform
        row.last_seen_at = datetime.now(timezone.utc)

    await db.flush()
    return row


async def get_caregiver_tokens(db: AsyncSession, patient_id: int) -> list[str]:
    """FCM tokens of the caregiver responsible for this patient.

    Follows ``users.caregiver_id``, the FK the schema already carries — no
    separate pairing table or invite code is needed. Returns [] when the patient
    has no caregiver on file or that caregiver has never opened the app.
    """
    caregiver_id = await db.scalar(
        select(User.caregiver_id).where(User.id == patient_id)
    )
    if caregiver_id is None:
        return []

    result = await db.execute(
        select(DeviceToken.token).where(DeviceToken.user_id == caregiver_id)
    )
    return list(result.scalars().all())


async def delete_device_token(db: AsyncSession, token: str) -> None:
    """Drop a token Firebase has rejected as unregistered (app uninstalled).

    Left in place it would fail on every future push and keep the error log
    warm forever.
    """
    result = await db.execute(select(DeviceToken).where(DeviceToken.token == token))
    row = result.scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.flush()


async def seconds_since_last_push(
    db: AsyncSession, patient_id: int, alert_type: str,
) -> float | None:
    """Age of the last push of this alert type, or None if there has never been one."""
    last = await db.scalar(
        select(PushNotification.sent_at)
        .where(PushNotification.patient_id == patient_id,
               PushNotification.alert_type == alert_type)
        .order_by(desc(PushNotification.sent_at))
        .limit(1)
    )
    if last is None:
        return None
    if last.tzinfo is None:            # SQLite drops tzinfo; Postgres keeps it
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds()


async def record_push(
    db: AsyncSession, patient_id: int, alert_id: int, alert_type: str,
    recipients: int,
) -> PushNotification:
    """Log a delivered push — this row is what the next cooldown check reads."""
    row = PushNotification(
        patient_id=patient_id,
        alert_id=alert_id,
        alert_type=alert_type,
        recipients=recipients,
        sent_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row
