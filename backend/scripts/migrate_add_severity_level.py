"""Idempotent migration: add users.severity_level (L2-4).

The caregiver states the patient's Alzheimer's stage (1 = early, 2 = moderate)
when creating them, and the report builds two different patient interfaces on
it. There was nowhere to put it — ``users`` held only id, firebase_uid, name,
role, caregiver_id, created_at.

**Why this script has to exist at all.** ``app/db/database.py:init_db`` calls
``Base.metadata.create_all``, which creates *missing tables* and nothing else.
The new ``pairing_codes`` table therefore appears by itself on the next boot,
but a new column on an existing table does not — on the Neon database
``users.severity_level`` would silently never be created, and every
``POST /api/patients`` would fail on a column that exists in the model and not
in the database. No alembic in this project, so: hand-written and re-runnable.

Run once against Neon before the next deploy:

    python -m scripts.migrate_add_severity_level
"""
import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        # Nullable on purpose: patients registered before this column existed
        # have no stated stage, and inventing one for them would be a clinical
        # claim nobody made.
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS severity_level INTEGER"
        ))
    print("OK: users.severity_level present (existing rows left NULL)")


if __name__ == "__main__":
    asyncio.run(main())
