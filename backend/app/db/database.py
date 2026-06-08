import os
from contextlib import asynccontextmanager

import firebase_admin
from firebase_admin import credentials, db as firebase_db
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

# ── PostgreSQL ────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/pathguard",
)

engine = create_async_engine(DATABASE_URL, echo=bool(os.getenv("DEBUG", False)))
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Create all tables on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Firebase Realtime DB ──────────────────────────────────────────────────────

_firebase_app: firebase_admin.App | None = None


def init_firebase() -> None:
    """Initialize Firebase Admin SDK (idempotent)."""
    global _firebase_app
    if _firebase_app is not None:
        return

    creds_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "./serviceAccountKey.json")
    firebase_url = os.getenv("FIREBASE_DATABASE_URL")

    cred = credentials.Certificate(creds_path)
    _firebase_app = firebase_admin.initialize_app(
        cred, {"databaseURL": firebase_url}
    )


def get_firebase_ref(path: str):
    """Return a Firebase Realtime DB reference for the given path."""
    return firebase_db.reference(path)
