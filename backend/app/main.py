# pathguard/backend/app/main.py
"""FastAPI application entry point.

Mounts every router except ``prediction``: its LSTM destination model pulls in
TensorFlow, which the 4-week plan cut (section 08) and which would add ~600 MB
to the image for an endpoint the app never calls.

⚠️ **Adding ``include_router(prediction.router)`` without installing TensorFlow
first does not break that one endpoint — it stops this whole application from
booting.** The import is top-level the entire way down:

    app/api/prediction.py:6
      → app/ai/module2_prediction/destination_prediction.py:16
        → app/ai/lstm_utils.py:6   import tensorflow as tf   ← ModuleNotFoundError

so the failure happens while ``app.main`` is being imported and takes ``/api/gps``,
``/api/sos`` and ``/api/pair`` down with it. (``module2_prediction/__init__.py``
has a lazy-import guard for exactly this; importing ``destination_prediction``
directly, as ``prediction.py`` does, walks straight past it.)

To bring the LSTM back, in this order: uncomment ``tensorflow`` in
requirements.txt → install it → then add the import and ``include_router`` line.

Nothing else in Module 2 needs TensorFlow. ``wandering_detection`` (Isolation
Forest), ``route_prediction`` (Markov + Viterbi) and ``stop_confusion_classification``
run on every GPS point today through ``module3_risk/risk_data_collection.py``, and
between them carry 75% of the risk score — so "Module 2 is not wired up" is a
statement about the LSTM alone, not about the module.

Run locally:  uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.database import init_db, init_firebase
from app.services.auth import log_startup_state
from app.api import (
    users, gps, recommendation, risk, search_area, admin_rules,
    places, danger_zones, devices, tracking, alerts, sos, pairing,
    trip_requests, destination,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure PostgreSQL tables exist on startup (idempotent).
    await init_db()
    log_startup_state()

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
app.include_router(sos.router)              # POST /api/sos
app.include_router(pairing.router)          # POST /api/patients, POST /api/pair, GET /api/patients/{id}
app.include_router(trip_requests.router)     # POST/GET/PATCH trip approval (C-3)
app.include_router(destination.router)      # GET  /api/predict-destination/{patient_id}


@app.get("/", summary="Service info")
async def root():
    return {"service": "PathGuard AI", "docs": "/docs"}


@app.get("/health", summary="Liveness probe for the cloud platform")
async def health():
    """Deliberately does not touch PostgreSQL: a DB blip should page us, not make
    the platform restart-loop a process that is otherwise healthy."""
    return {"status": "ok"}
