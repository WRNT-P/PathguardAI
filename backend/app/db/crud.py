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
    Alert, BehavioralProfile, CaregiverInvite, DeviceToken, GPSData,
    PairingCode, PatientCaregiver, PushNotification, RiskScore, TripRequest,
    User,
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


async def get_user(db: AsyncSession, user_id: int) -> User | None:
    """The whole row, for the callers that need more than existence.

    Device pairing needs ``firebase_uid`` to mint a custom token against, which
    ``user_exists`` cannot give it.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    firebase_uid: str,
    name: str,
    role: str,
    caregiver_id: int | None = None,
    severity_level: int | None = None,
) -> User:
    """Create a user row (the FK target GPS/risk/alert data references).

    Written by the register endpoint so a ``users.id`` exists before any GPS for
    that patient arrives, and by ``POST /api/patients`` when a caregiver creates
    a patient who has no Firebase account yet. Caller should check
    ``get_user_id_by_firebase_uid`` first to keep ``firebase_uid`` unique.
    """
    user = User(
        firebase_uid=firebase_uid,
        name=name,
        role=role,
        severity_level=severity_level,
    )
    db.add(user)
    await db.flush()
    # ``caregiver_id`` is still the argument callers pass — it was a column on
    # ``users`` until 2026-08-28 and is now the first row in patient_caregivers.
    # Whoever creates the patient is the primary caregiver.
    if caregiver_id is not None:
        await link_caregiver(db, user.id, caregiver_id, is_primary=True)
    return user


async def link_caregiver(
    db: AsyncSession, patient_id: int, caregiver_id: int,
    is_primary: bool = False,
) -> PatientCaregiver | None:
    """Make ``caregiver_id`` responsible for ``patient_id``. Idempotent.

    Returns None when the link already exists, so a caregiver added twice is a
    no-op rather than a duplicate push and a duplicate row in the distance
    ranking. The unique constraint enforces the same thing at the database, but
    hitting it would abort the whole request's transaction.
    """
    existing = await db.scalar(
        select(PatientCaregiver).where(
            PatientCaregiver.patient_id == patient_id,
            PatientCaregiver.caregiver_id == caregiver_id,
        )
    )
    if existing is not None:
        return None
    row = PatientCaregiver(patient_id=patient_id, caregiver_id=caregiver_id,
                           is_primary=is_primary)
    db.add(row)
    await db.flush()
    return row


# ── Device pairing ───────────────────────────────────────────────────────────

async def create_pairing_code(
    db: AsyncSession, code: str, patient_id: int, expires_at: datetime,
) -> PairingCode:
    """Store one unredeemed code. ``code`` must already be normalised."""
    row = PairingCode(code=code, patient_id=patient_id, expires_at=expires_at)
    db.add(row)
    await db.flush()
    return row


async def get_pairing_code(db: AsyncSession, code: str) -> PairingCode | None:
    """Look up a code regardless of whether it is expired or already spent.

    The endpoint decides what to tell the caller; keeping the three cases apart
    here is what lets the logs say which one happened while the response does
    not.
    """
    result = await db.execute(select(PairingCode).where(PairingCode.code == code))
    return result.scalar_one_or_none()


async def mark_pairing_code_used(
    db: AsyncSession, row: PairingCode, now: datetime | None = None,
) -> PairingCode:
    """Spend a code. Single use is enforced by the caller checking ``used_at``."""
    row.used_at = now or datetime.now(timezone.utc)
    await db.flush()
    return row


async def create_caregiver_invite(
    db: AsyncSession, code: str, patient_id: int, invited_by: int | None,
    expires_at: datetime,
) -> CaregiverInvite:
    """Store one unredeemed caregiver invite. ``code`` must be normalised."""
    row = CaregiverInvite(code=code, patient_id=patient_id,
                          invited_by=invited_by, expires_at=expires_at)
    db.add(row)
    await db.flush()
    return row


async def get_caregiver_invite(
    db: AsyncSession, code: str,
) -> CaregiverInvite | None:
    """Look up an invite whether or not it is expired or spent.

    Deliberately does not fall back to ``pairing_codes``: those two code spaces
    stay separate so a code meant to set up a patient's phone can never be
    redeemed for access to that patient's data.
    """
    result = await db.execute(
        select(CaregiverInvite).where(CaregiverInvite.code == code))
    return result.scalar_one_or_none()


async def mark_caregiver_invite_used(
    db: AsyncSession, row: CaregiverInvite, now: datetime | None = None,
) -> CaregiverInvite:
    """Spend an invite. Single use is enforced by the caller checking ``used_at``."""
    row.used_at = now or datetime.now(timezone.utc)
    await db.flush()
    return row


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
    """FCM tokens of every caregiver responsible for this patient.

    Every device of every caregiver, not one: since 2026-08-28 a patient can
    have more than one, and the report has always said an alert goes to all of
    them. Returns [] when the patient has no caregiver on file or none of them
    has ever opened the app.
    """
    caregiver_ids = await get_caregiver_ids(db, patient_id)
    if not caregiver_ids:
        return []

    result = await db.execute(
        select(DeviceToken.token).where(DeviceToken.user_id.in_(caregiver_ids))
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


# ── Tracking / alert reads (caregiver app) ───────────────────────────────────

async def get_recent_track(
    db: AsyncSession, patient_id: int, hours: int = 6, fallback_limit: int = 300,
) -> list[GPSData]:
    """The patient's last ``hours`` of movement, oldest first.

    Falls back to the most recent ``fallback_limit`` readings when that window
    holds fewer than two points. A caregiver who opens the map after a push
    needs to see *something*: a phone that has been off since this morning would
    otherwise render an empty screen, which reads as "no data" when the truth is
    "no data recently".
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(GPSData)
        .where(GPSData.patient_id == patient_id, GPSData.recorded_at >= since)
        .order_by(GPSData.recorded_at)
    )
    rows = list(result.scalars().all())
    if len(rows) >= 2:
        return rows

    result = await db.execute(
        select(GPSData)
        .where(GPSData.patient_id == patient_id)
        .order_by(desc(GPSData.recorded_at))
        .limit(fallback_limit)
    )
    return list(reversed(result.scalars().all()))


async def get_alerts(
    db: AsyncSession, patient_id: int, limit: int = 20,
) -> list[Alert]:
    """A patient's alerts, newest first — the caregiver's history feed."""
    result = await db.execute(
        select(Alert)
        .where(Alert.patient_id == patient_id)
        .order_by(desc(Alert.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def set_alert_resolved(
    db: AsyncSession, alert_id: int, resolved: bool,
) -> Alert | None:
    """Mark an alert handled (or un-handle it). None if there is no such alert."""
    alert = await db.get(Alert, alert_id)
    if alert is None:
        return None
    alert.resolved = resolved
    alert.resolved_at = datetime.now(timezone.utc) if resolved else None
    await db.flush()
    return alert


async def update_user_location(
    db: AsyncSession, user_id: int, latitude: float, longitude: float,
) -> User | None:
    """Overwrite where this user last was. Returns None if there is no such user.

    Overwrite, not append: only the current position is kept (see the columns'
    comment in models.py). ``location_updated_at`` is set server-side rather
    than taken from the caller so a phone with a wrong clock cannot make a stale
    position look fresh and win a distance ranking it should have lost.
    """
    user = await db.get(User, user_id)
    if user is None:
        return None
    user.last_latitude = latitude
    user.last_longitude = longitude
    user.location_updated_at = datetime.now(timezone.utc)
    await db.flush()
    return user


async def get_caregiver_ids(db: AsyncSession, patient_id: int) -> list[int]:
    """Every caregiver responsible for this patient, primary first.

    The order is stable and meaningful: callers that must name one person (a
    profile screen, a distance-ranking tie-break) take the first, and callers
    that notify everybody take the list. Empty when nobody is linked.
    """
    result = await db.execute(
        select(PatientCaregiver.caregiver_id)
        .where(PatientCaregiver.patient_id == patient_id)
        .order_by(desc(PatientCaregiver.is_primary), PatientCaregiver.id)
    )
    return list(result.scalars().all())


async def get_caregiver_id(db: AsyncSession, patient_id: int) -> int | None:
    """The *primary* caregiver, or None if nobody is responsible.

    Kept for the places that genuinely need one person rather than the set —
    ``caregiver_id`` in an API response, for instance. Anything deciding who to
    notify or who may read a patient's data must use ``get_caregiver_ids``:
    since 2026-08-28 this function answers with one name out of possibly several
    and is the wrong question for authorization.
    """
    ids = await get_caregiver_ids(db, patient_id)
    return ids[0] if ids else None


async def get_alert(db: AsyncSession, alert_id: int) -> Alert | None:
    """One alert by id — the authorization check needs its ``patient_id``."""
    return await db.get(Alert, alert_id)


# ── Trip approval (report C-3) ───────────────────────────────────────────────

async def create_trip_request(
    db: AsyncSession,
    patient_id: int,
    destination_name: str,
    latitude: float,
    longitude: float,
    confidence: float,
    factors: str | None = None,
) -> TripRequest:
    """Record one request, with the confidence as it stood when it was asked."""
    row = TripRequest(
        patient_id=patient_id,
        destination_name=destination_name,
        latitude=latitude,
        longitude=longitude,
        confidence=confidence,
        factors=factors,
        status="pending",
    )
    db.add(row)
    await db.flush()
    return row


async def get_trip_request(db: AsyncSession, request_id: int) -> TripRequest | None:
    result = await db.execute(
        select(TripRequest).where(TripRequest.id == request_id))
    return result.scalar_one_or_none()


async def get_trip_requests(
    db: AsyncSession, patient_id: int, status: str | None = None, limit: int = 20,
) -> list[TripRequest]:
    """Newest first. ``status`` filters to pending/approved/rejected."""
    stmt = select(TripRequest).where(TripRequest.patient_id == patient_id)
    if status is not None:
        stmt = stmt.where(TripRequest.status == status)
    result = await db.execute(
        stmt.order_by(desc(TripRequest.created_at), desc(TripRequest.id)).limit(limit))
    return list(result.scalars().all())


async def decide_trip_request(
    db: AsyncSession, row: TripRequest, status: str, decided_by: int | None,
) -> TripRequest:
    """Approve or reject. The caller has already checked the row is pending."""
    row.status = status
    row.decided_by = decided_by
    row.decided_at = datetime.now(timezone.utc)
    await db.flush()
    return row
