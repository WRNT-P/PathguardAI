from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey,
    Index, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "patient" | "caregiver"
    # NOTE: ``caregiver_id`` lived here until 2026-08-28 — one nullable FK, so a
    # patient could have exactly one caregiver. The report has always promised
    # "alert every caregiver, ranked by distance", and the app side confirmed
    # they want it, so the link moved to ``patient_caregivers`` below. The column
    # is deliberately NOT dropped from the Neon database (see
    # scripts/migrate_add_patient_caregivers.py): it is the only copy of the
    # pre-migration state, and it is invisible to the ORM once it is gone from
    # here, which is enough to stop anything reading it by accident.
    # 1 = early stage, 2 = moderate. The caregiver states it when creating the
    # patient; the report builds two different patient interfaces on it. Nothing
    # in app/ai reads it — a severity multiplier on the Module 3 weights would be
    # the only number in the rule KB without a citation behind it.
    severity_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Where this CAREGIVER last was. Added 2026-08-28 for the report's "alert
    # every caregiver, ranked by distance" — you cannot rank by distance without
    # knowing where the people are.
    #
    # Latest position only, deliberately: no history table, no trail. Ranking
    # asks "who is nearest right now" and nothing asks anything else, so keeping
    # a track would be surveillance of a family member who is not the patient
    # and has not consented to being followed — a privacy cost with no feature
    # behind it. ``location_updated_at`` exists because a position from
    # yesterday must not win a ranking; whoever ranks decides how stale is too
    # stale.
    #
    # Null for every patient (their positions live in gps_data, which is a
    # different question with different retention) and for any caregiver whose
    # app has not reported yet.
    last_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    gps_records: Mapped[list["GPSData"]] = relationship("GPSData", back_populates="patient", cascade="all, delete-orphan")
    risk_scores: Mapped[list["RiskScore"]] = relationship("RiskScore", back_populates="patient", cascade="all, delete-orphan")
    alerts: Mapped[list["Alert"]] = relationship("Alert", back_populates="patient", cascade="all, delete-orphan")
    behavioral_profiles: Mapped[list["BehavioralProfile"]] = relationship("BehavioralProfile", back_populates="patient", cascade="all, delete-orphan")



class PatientCaregiver(Base):
    """Which caregivers are responsible for a patient. Many-to-many.

    Replaces the single ``users.caregiver_id`` FK on 2026-08-28. Three things in
    the report need this and none of them could be built on one FK: alerting
    every caregiver, ranking them by distance, and one of them claiming "I'll
    go and get them".

    ``is_primary`` is the caregiver who created the patient through
    ``POST /api/patients``. It is not a permission level — every row here grants
    the same access — it exists because some questions have to have exactly one
    answer: who is shown as *the* caregiver on a profile, and who breaks a tie
    when two caregivers are the same distance away.
    """
    __tablename__ = "patient_caregivers"
    __table_args__ = (
        # A caregiver linked to the same patient twice would be pushed to twice
        # and would appear twice in a distance ranking.
        UniqueConstraint("patient_id", "caregiver_id",
                         name="uq_patient_caregiver"),
        Index("ix_patient_caregivers_patient", "patient_id"),
        Index("ix_patient_caregivers_caregiver", "caregiver_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)
    caregiver_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class GPSData(Base):
    """30-day GPS history stored in PostgreSQL. Live position goes to Firebase."""
    __tablename__ = "gps_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)   # metres
    speed: Mapped[float | None] = mapped_column(Float, nullable=True)      # m/s
    altitude: Mapped[float | None] = mapped_column(Float, nullable=True)   # metres
    direction: Mapped[float | None] = mapped_column(Float, nullable=True)  # degrees 0–359
    device_motion: Mapped[str | None] = mapped_column(String(20), nullable=True)  # e.g. "walking" | "still"
    # Kalman-smoothed coords stored alongside raw
    smooth_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    smooth_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # True for synthetically-injected wandering points (issue #1 Phase 2.5) so
    # real GeoLife points and injected anomalies stay auditable/separable.
    synthetic_injected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    patient: Mapped["User"] = relationship("User", back_populates="gps_records")


class RiskScore(Base):
    __tablename__ = "risk_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)            # 0–100
    level: Mapped[str] = mapped_column(String(10), nullable=False)         # "low" | "medium" | "high"
    wandering_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    gps_available: Mapped[bool] = mapped_column(Boolean, default=True)
    factors: Mapped[str | None] = mapped_column(Text, nullable=True)       # JSON string
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    patient: Mapped["User"] = relationship("User", back_populates="risk_scores")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)    # "wandering" | "geofence" | "gps_loss" | "emergency"
    severity: Mapped[str] = mapped_column(String(10), nullable=False)      # "low" | "medium" | "high" | "critical"
    message: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    patient: Mapped["User"] = relationship("User", back_populates="alerts")


class BehavioralProfile(Base):
    """Stores clustered places and daily routine patterns per patient for AI modules."""
    __tablename__ = "behavioral_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, unique=True)
    known_places: Mapped[str | None] = mapped_column(Text, nullable=True)          # JSON: [{lat, lon, label, visits}]
    routine_patterns: Mapped[str | None] = mapped_column(Text, nullable=True)      # JSON: [{hour, cluster_id, probability, samples}] — ai/module1_behavior/routine_patterns.py
    typical_range_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    patient: Mapped["User"] = relationship("User", back_populates="behavioral_profiles")


# ── Module 3 rule knowledge base ──────────────────────────────────────────────
# Rules live in the DB (not code) so judges can inspect values, medical sources
# and rationale at runtime. Updates never mutate rows: the old row is flipped to
# active=False and a new row (version+1) is inserted, so full history is kept.
# "Exactly one active row per name" is enforced by rule_repository (app-level,
# per design Q1 — works identically on SQLite tests and Postgres).


class RiskFactorWeight(Base):
    """One weighted factor of the Module 3 risk formula (weights sum to 1.0)."""
    __tablename__ = "risk_factor_weights"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    factor_name: Mapped[str] = mapped_column(String(50), nullable=False)   # validated vs KNOWN_FACTORS
    weight: Mapped[float] = mapped_column(Float, nullable=False)           # 0–1; active set sums to 1.0
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g. "TH-DMS-2564 §BPSD"
    rationale: Mapped[str] = mapped_column(Text, nullable=False)           # judge-readable medical justification
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_weight_name_active", "factor_name", "active"),)


class RiskThreshold(Base):
    """A named cut-off used by Module 3 (score boundaries, distances, timeouts)."""
    __tablename__ = "risk_thresholds"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    threshold_name: Mapped[str] = mapped_column(String(50), nullable=False)  # validated vs KNOWN_THRESHOLDS
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)            # "score" | "meter" | "second"
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_threshold_name_active", "threshold_name", "active"),)


class DangerZone(Base):
    """A geofenced circle that forces an emergency when the patient is inside."""
    __tablename__ = "danger_zones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    center_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    center_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    radius_meters: Mapped[float] = mapped_column(Float, nullable=False)
    zone_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "highway" | "waterway" | "construction" | "other"
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # True for demo/seeded hazards injected by inject_wandering.py (issue #1
    # Phase 2.5) so a synthetic zone is never mistaken for a real KB hazard.
    synthetic_injected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_danger_zone_active", "active"),)


class RuleAuditLog(Base):
    """Immutable trail of every rule change — written in the SAME transaction
    as the change itself (design Q4), so log and rule state can never disagree."""
    __tablename__ = "rule_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(String(50), nullable=False)
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)   # id of the NEW active row
    field_changed: Mapped[str] = mapped_column(String(50), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(255), nullable=True)   # None for inserts
    new_value: Mapped[str | None] = mapped_column(String(255), nullable=True)   # None for deactivations
    changed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str] = mapped_column(Text, nullable=False)            # NOT NULL: no anonymous rule changes


class TemporalRule(Base):
    """A rule that uses a patient's risk-score HISTORY (not just the current
    reading) to adjust the score or force an escalation. Same KB discipline as
    the other rule tables: values live in ``parameters`` (JSON) — nothing is
    hardcoded in the pure ``temporal_adjustment`` engine."""
    __tablename__ = "temporal_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_name: Mapped[str] = mapped_column(String(50), nullable=False)   # validated vs KNOWN_TEMPORAL_RULES
    # Tunables per rule, e.g. {"window": 3, "boost": 10} / {"window": 5, "min_score": 50}
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_temporal_rule_name_active", "rule_name", "active"),)


# ── Push notification (Module: caregiver alerting) ────────────────────────────

class DeviceToken(Base):
    """An FCM registration token for one caregiver device.

    A caregiver may sign in on more than one phone, so this is many-per-user.
    The token itself is unique — Firebase reissues it on reinstall, and the app
    re-POSTs on every launch, so ``POST /api/devices/token`` upserts.
    """
    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)     # "android" | "ios" | "web"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_device_token_user", "user_id"),)


class PushNotification(Base):
    """One row per push actually sent — and the state the send cooldown reads.

    ``alerts`` deliberately stays untouched: risk.py writes a row every scoring
    round a condition holds, which at the 60 s ingest throttle means one row a
    minute for as long as a patient sits in a danger zone. That is the right
    audit trail, but it is not a push schedule. Rate limiting lives here instead,
    so nothing about how alerts are recorded has to change.
    """
    __tablename__ = "push_notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    alert_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("alerts.id"), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)   # cooldown is per type
    recipients: Mapped[int] = mapped_column(Integer, nullable=False)      # devices reached
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_push_patient_type_sent", "patient_id", "alert_type", "sent_at"),)


class PairingCode(Base):
    """The short code a caregiver reads off their screen and types into the
    patient's phone — and the only thing standing between the two devices.

    A patient with dementia cannot be asked to hold an email address and a
    password, so the patient device never signs itself up. The caregiver creates
    the patient, the server picks that patient's Firebase uid up front, and this
    row is the one-time claim on it. ``POST /api/pair`` trades the code for a
    Firebase **custom token**; the app signs in with it and sends an ordinary
    bearer token from then on, so ``services/auth.py`` needs no second code path
    and its tests keep covering the only one there is.

    Why the code is eight characters and not six digits: the thing behind this
    door is a dementia patient's live position, and a six-digit code is a million
    guesses. There is no distributed rate limiter here to make that safe — one
    process-local counter would be a lie the moment a second worker starts — so
    the safety comes from entropy instead. Eight characters of ``_ALPHABET``
    (32 symbols, ambiguous ones removed) is ~1.1e12 codes, which at a hundred
    guesses a second is several centuries, and a caregiver still only types it
    once. Expiry and single use are what limit the damage if a code leaks; they
    are not what stops guessing.
    """
    __tablename__ = "pairing_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Stored normalised (upper case, no separator) — see api/pairing.normalise.
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set the moment the code is spent. A redeemed code is kept rather than
    # deleted so "this code was already used" stays distinguishable from "no such
    # code" in the logs, without the response telling an outsider which it was.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_pairing_code_patient", "patient_id"),)


class TripRequest(Base):
    """A Level 2 patient asking to go somewhere they have never been (report C-3).

    The report locks a Level 2 patient's search box and routes the request
    through their caregiver, who sees a confidence score and a map preview
    before deciding. ``confidence`` and ``factors`` are stored on the row rather
    than recomputed when the caregiver opens it, because the caregiver is
    deciding about the moment the patient asked — a score that drifted while the
    phone sat in a pocket would be a different question than the one asked.

    ``status`` is ``pending`` until somebody decides. There is deliberately no
    automatic timeout: a request that expired on its own would look identical to
    one nobody ever saw, and the difference matters to a family.
    """
    __tablename__ = "trip_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    destination_name: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # 0–1 from module5_recommend.trip_confidence, frozen at request time.
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # JSON — the familiarity/danger_zone breakdown, so the screen can say WHY.
    factors: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_trip_request_patient_status", "patient_id", "status"),)
