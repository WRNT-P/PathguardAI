from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "patient" | "caregiver"
    caregiver_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    gps_records: Mapped[list["GPSData"]] = relationship("GPSData", back_populates="patient", cascade="all, delete-orphan")
    risk_scores: Mapped[list["RiskScore"]] = relationship("RiskScore", back_populates="patient", cascade="all, delete-orphan")
    alerts: Mapped[list["Alert"]] = relationship("Alert", back_populates="patient", cascade="all, delete-orphan")
    behavioral_profiles: Mapped[list["BehavioralProfile"]] = relationship("BehavioralProfile", back_populates="patient", cascade="all, delete-orphan")


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
    # Kalman-smoothed coords stored alongside raw
    smooth_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    smooth_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    routine_patterns: Mapped[str | None] = mapped_column(Text, nullable=True)      # JSON: [{hour, place_id, probability}]
    typical_range_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    patient: Mapped["User"] = relationship("User", back_populates="behavioral_profiles")
