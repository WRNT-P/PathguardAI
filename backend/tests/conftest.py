"""Shared pytest fixtures for the PathGuard backend test suite.

Provides an in-memory SQLite database (via aiosqlite) that reuses the real
SQLAlchemy models/metadata, plus an httpx client wired to the FastAPI app with
the DB dependency overridden. No PostgreSQL or Firebase is required to run any
test here — importing ``app.db.database`` builds the Postgres engine lazily and
never connects, and Firebase is only initialised on demand (never in tests).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
import app.db.models  # noqa: F401  — register all ORM tables on Base.metadata


# SQLite only autoincrements INTEGER PRIMARY KEY, not BIGINT. The models use
# BigInteger PKs (correct for Postgres); render them as INTEGER on SQLite so the
# in-memory test DB assigns ids automatically.
@compiles(BigInteger, "sqlite")
def _bigint_as_integer_on_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "INTEGER"


@pytest_asyncio.fixture
async def db_engine():
    """Fresh in-memory SQLite engine per test (StaticPool keeps one shared conn)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Seed the Module 3 rule knowledge base — the /api/risk pipeline loads
    # weights/thresholds/danger-zones from these tables on every request.
    from app.mock.seed_risk_rules import seed_rules

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await seed_rules(session)
        await session.commit()
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory) -> AsyncSession:
    """A committed-as-you-go session for seeding/asserting DB state in tests."""
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(session_factory):
    """httpx AsyncClient bound to a test app with get_db -> test SQLite.

    The app mounts the TensorFlow-free routers (the real handler code). The
    ``prediction`` router is excluded because it imports the LSTM
    ``DestinationPredictor`` (TensorFlow), which is not installed in this env.
    ASGITransport does not run a lifespan, so the Postgres ``init_db()`` hook
    never fires.
    """
    import httpx
    from fastapi import FastAPI

    from app.api import (
        admin_rules, alerts, danger_zones, devices, gps, pairing, places,
        recommendation, risk, search_area, sos, tracking, trip_requests, users,
    )

    app = FastAPI(title="PathGuard AI (test)")
    app.include_router(users.router)
    app.include_router(gps.router)
    app.include_router(recommendation.router)
    app.include_router(risk.router)
    app.include_router(search_area.router)
    app.include_router(admin_rules.router)
    app.include_router(places.router)
    app.include_router(danger_zones.router)
    app.include_router(devices.router)
    app.include_router(tracking.router)
    app.include_router(alerts.router)
    app.include_router(sos.router)
    app.include_router(pairing.router)
    app.include_router(trip_requests.router)

    @app.get("/")
    async def _root():
        return {"service": "PathGuard AI", "docs": "/docs"}

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
