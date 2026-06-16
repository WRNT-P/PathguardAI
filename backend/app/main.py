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
from app.api import users, gps


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
app.include_router(users.router)   # POST /api/register
app.include_router(gps.router)     # POST /api/gps

# TODO (teammates): mount your module routers here once they exist, e.g.
#   from app.api import risk, search_area, recommendation
#   app.include_router(risk.router)
#   app.include_router(search_area.router)
#   app.include_router(recommendation.router)


@app.get("/", summary="Service info")
async def root():
    return {"service": "PathGuard AI", "docs": "/docs"}
