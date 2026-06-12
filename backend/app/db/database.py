import os
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlsplit, urlunsplit

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


def _normalize_url(raw: str) -> tuple[str, dict]:
    """Accept a standard Neon/libpq URL and adapt it for the asyncpg driver.

    Lets teammates paste their raw Neon string (``postgres://…?sslmode=require``)
    unchanged: we force the ``+asyncpg`` driver and translate the libpq-only
    ``sslmode``/``channel_binding`` params — which asyncpg rejects — into a
    ``connect_args`` SSL flag.
    """
    parts = urlsplit(raw)
    scheme = "postgresql+asyncpg" if parts.scheme in ("postgres", "postgresql") else parts.scheme
    query = dict(parse_qsl(parts.query))

    sslmode = query.pop("sslmode", None)
    ssl_flag = query.pop("ssl", None)
    query.pop("channel_binding", None)  # libpq-only, asyncpg can't use it

    connect_args: dict = {}
    if sslmode in ("require", "verify-ca", "verify-full") or ssl_flag in ("require", "true"):
        connect_args["ssl"] = True

    new_query = "&".join(f"{k}={v}" for k, v in query.items())
    url = urlunsplit((scheme, parts.netloc, parts.path, new_query, parts.fragment))
    return url, connect_args


_url, _connect_args = _normalize_url(DATABASE_URL)
engine = create_async_engine(
    _url, echo=bool(os.getenv("DEBUG", False)), connect_args=_connect_args
)
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
