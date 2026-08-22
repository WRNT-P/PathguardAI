# pathguard/backend/app/main.py
"""FastAPI application entry point.

Mounts all routers except ``prediction``: its LSTM destination model pulls in
TensorFlow, which the 4-week plan cut (section 08) and which would add ~600 MB
to the deployed image for an endpoint the app never calls. ``app/api/prediction.py``
is left in place — re-add the import and ``include_router`` line to bring it back.

Run locally:  uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.database import init_db, init_firebase
from app.api import (
    users, gps, recommendation, risk, search_area, admin_rules,
    places, danger_zones, devices, tracking, alerts,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure PostgreSQL tables exist on startup (idempotent).
    await init_db()

    # Firebase carries the live caregiver map only; PostgreSQL is the source of
    # truth. Starting without serviceAccountKey.json is a supported dev mode, so
    # a missing/bad key degrades to "no live map", never a failed boot.
    try:
        init_firebase()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Firebase not initialised (%s) — live position push disabled", exc
        )

    yield


app = FastAPI(title="PathGuard AI", lifespan=lifespan)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(users.router)            # POST /api/register
app.include_router(gps.router)              # POST /api/gps, /api/gps/batch
app.include_router(recommendation.router)   # GET  /api/recommendation/{patient_id}
app.include_router(risk.router)             # GET  /api/risk/{patient_id}
app.include_router(search_area.router)      # GET  /api/search-area/{patient_id}
app.include_router(admin_rules.router)      # GET  /api/admin/rules, /api/admin/rules/history
app.include_router(places.router)           # POST/GET /api/patients/{id}/places
app.include_router(danger_zones.router)     # POST/GET/DELETE /api/danger-zones
app.include_router(devices.router)          # POST /api/devices/token
app.include_router(tracking.router)         # GET  /api/patients/{id}/track
app.include_router(alerts.router)           # GET  /api/patients/{id}/alerts, PATCH /api/alerts/{id}


@app.get("/", summary="Service info")
async def root():
    return {"service": "PathGuard AI", "docs": "/docs"}


@app.get("/health", summary="Liveness probe for the cloud platform")
async def health():
    """Deliberately does not touch PostgreSQL: a DB blip should page us, not make
    the platform restart-loop a process that is otherwise healthy."""
    return {"status": "ok"}
