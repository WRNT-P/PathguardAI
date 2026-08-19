# pathguard/backend/scripts/demo_server.py
"""Demo web server — caregiver dashboard on real data, TF-free.

Mounts every real API router except Module 2's ``prediction`` (its LSTM path
imports TensorFlow, which isn't installed in this venv), and serves the
single-page dashboard (``dashboard.html``) plus a small read-only ``/demo``
layer the page needs for the map: recent track points, alert feed, and patient
header info. No app/ code is changed — this file composes what already exists.

Run:   venv\\Scripts\\python.exe -m uvicorn scripts.demo_server:app --port 8000
Open:  http://127.0.0.1:8000        (dashboard)
       http://127.0.0.1:8000/docs   (Swagger UI — try the raw APIs)
"""
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import admin_rules, gps, recommendation, risk, search_area, users
from app.db.database import get_db, init_db
from app.db.models import Alert, BehavioralProfile, GPSData, User

_HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="PathGuard AI — Demo Dashboard", lifespan=lifespan)

for module in (users, gps, recommendation, risk, search_area, admin_rules):
    app.include_router(module.router)


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(_HERE / "dashboard.html", media_type="text/html")


@app.get("/health", include_in_schema=False)
async def health():
    """Liveness probe for Railway/Render — mirrors app.main, no DB touched."""
    return {"status": "ok"}


@app.get("/demo/patient/{patient_id}", summary="Header info for the dashboard")
async def demo_patient(patient_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, patient_id)
    total = (await db.execute(
        select(func.count()).select_from(GPSData)
        .where(GPSData.patient_id == patient_id))).scalar_one()
    injected = (await db.execute(
        select(func.count()).select_from(GPSData)
        .where(GPSData.patient_id == patient_id,
               GPSData.synthetic_injected.is_(True)))).scalar_one()
    last_at = (await db.execute(
        select(func.max(GPSData.recorded_at))
        .where(GPSData.patient_id == patient_id))).scalar_one()
    profile = (await db.execute(
        select(BehavioralProfile)
        .where(BehavioralProfile.patient_id == patient_id))).scalars().first()
    places = 0
    if profile and profile.known_places:
        try:
            places = len(json.loads(profile.known_places))
        except (json.JSONDecodeError, TypeError):
            places = 0
    return {
        "patient_id": patient_id,
        "name": user.name if user else f"patient {patient_id}",
        "firebase_uid": user.firebase_uid if user else None,
        "total_points": total,
        "injected_points": injected,
        "real_points": total - injected,
        "known_places": places,
        "last_recorded_at": last_at.isoformat() if last_at else None,
    }


@app.get("/demo/track/{patient_id}", summary="Recent GPS points for the map")
async def demo_track(patient_id: int, hours: int = 6,
                     db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (await db.execute(
        select(GPSData)
        .where(GPSData.patient_id == patient_id, GPSData.recorded_at >= since)
        .order_by(GPSData.recorded_at))).scalars().all()
    if len(rows) < 2:  # no recent session — fall back to the latest 300 points
        rows = list(reversed((await db.execute(
            select(GPSData).where(GPSData.patient_id == patient_id)
            .order_by(GPSData.recorded_at.desc()).limit(300))).scalars().all()))
    return {
        "patient_id": patient_id,
        "count": len(rows),
        "points": [
            {"lat": r.latitude, "lng": r.longitude,
             "t": r.recorded_at.isoformat(),
             "injected": r.synthetic_injected, "speed": r.speed}
            for r in rows
        ],
    }


@app.get("/demo/alerts/{patient_id}", summary="Latest alerts, newest first")
async def demo_alerts(patient_id: int, limit: int = 10,
                      db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Alert).where(Alert.patient_id == patient_id)
        .order_by(Alert.created_at.desc()).limit(limit))).scalars().all()
    return [
        {"id": a.id, "type": a.alert_type, "severity": a.severity,
         "message": a.message, "lat": a.latitude, "lng": a.longitude,
         "resolved": a.resolved, "created_at": a.created_at.isoformat()}
        for a in rows
    ]
