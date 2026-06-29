# pathguard/backend/app/main.py
"""FastAPI application entry point.


Mounts the routers that exist today (users, gps). Teammates register their own
module routers (risk, search_area, recommendation) where marked below, once
those routers exist.

Run locally:  uvicorn app.main:app --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.database import init_db
from app.api import users, gps, recommendation, prediction, risk, search_area


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure PostgreSQL tables exist on startup (idempotent).
    # NOTE: Firebase (init_firebase) is intentionally NOT initialized here yet —
    # it needs serviceAccountKey.json. The live GPS push is best-effort, so the
    # API runs fine without it during development.
    await init_db()
    yield


app = FastAPI(title="PathGuard AI", lifespan=lifespan)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(users.router)            # POST /api/register
app.include_router(gps.router)              # POST /api/gps
app.include_router(recommendation.router)   # GET  /api/recommendation/{patient_id}
app.include_router(prediction.router)       # GET  /api/predict-destination/{patient_id}
app.include_router(risk.router)             # GET  /api/risk/{patient_id}
app.include_router(search_area.router)      # GET  /api/search-area/{patient_id}


@app.get("/", summary="Service info")
async def root():
    return {"service": "PathGuard AI", "docs": "/docs"}
