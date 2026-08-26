# pathguard/backend/scripts/demo_server.py
"""Demo web server — caregiver dashboard on real data, TF-free.

Mounts every real API router except Module 2's ``prediction`` (its LSTM path
imports TensorFlow, which isn't installed in this venv), and serves the
single-page dashboard (``dashboard.html``).

Track and alert feeds used to live here as ``/demo/track`` and ``/demo/alerts``.
They were never fake — "demo" was a URL prefix over the real tables — so when the
caregiver app needed the same reads they were promoted to ``app/api/tracking.py``
and ``app/api/alerts.py`` rather than copied. The dashboard now calls those, the
same endpoints the phone does. What is left under ``/demo`` is the one thing that
really is demo-only: a header counting injected versus real points.

Run:   venv\\Scripts\\python.exe -m uvicorn scripts.demo_server:app --port 8000
Open:  http://127.0.0.1:8000        (dashboard)
       http://127.0.0.1:8000/docs   (Swagger UI — try the raw APIs)
"""
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import (
    admin_rules, alerts, danger_zones, devices, gps, pairing, places,
    recommendation, risk, search_area, sos, tracking, trip_requests, users,
)
from app.db.database import get_db, init_db
from app.db.models import BehavioralProfile, GPSData, User
from app.services import auth
from app.services.auth import Caller, verify_patient_access

_HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # This process mounts the same guarded routers app.main does, and the page it
    # serves has no Firebase sign-in — so with auth on, every panel on the
    # dashboard 401s. Say so at boot rather than letting it look like a bug.
    if auth.AUTH_ENABLED:
        logging.getLogger(__name__).warning(
            "AUTH_ENABLED is on and dashboard.html cannot sign in — every panel "
            "will fail with 401. Run this process with AUTH_ENABLED=false, on "
            "localhost only, and point the tunnel at app.main instead."
        )
    yield


app = FastAPI(title="PathGuard AI — Demo Dashboard", lifespan=lifespan)

# Keep this list identical to app/main.py's. It was short of `places` and five
# others, so a caregiver pin sent at the dashboard's port came back 404 while the
# same request against app.main worked — a difference nothing announced.
for module in (users, gps, recommendation, risk, search_area, admin_rules,
               places, danger_zones, devices, tracking, alerts, sos, pairing,
               trip_requests):
    app.include_router(module.router)


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(_HERE / "dashboard.html", media_type="text/html")


@app.get("/health", include_in_schema=False)
async def health():
    """Liveness probe for Railway/Render — mirrors app.main, no DB touched."""
    return {"status": "ok"}


@app.get("/demo/patient/{patient_id}", summary="Header info for the dashboard")
async def demo_patient(
    patient_id: int,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
):
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


