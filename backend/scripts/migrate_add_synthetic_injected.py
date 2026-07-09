"""Idempotent migration: add gps_data.synthetic_injected (issue #1 Phase 2.5).

No alembic in this project, so this is a hand-written, re-runnable migration.
`ADD COLUMN IF NOT EXISTS` makes it safe to run on the existing Neon DB (which
already has rows) and a no-op on a fresh DB that got the column via create_all.

Run:  python -m scripts.migrate_add_synthetic_injected
"""
import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        for table in ("gps_data", "danger_zones"):
            await conn.execute(text(
                f"ALTER TABLE {table} "
                "ADD COLUMN IF NOT EXISTS synthetic_injected BOOLEAN NOT NULL DEFAULT FALSE"
            ))
    print("OK: synthetic_injected present on gps_data + danger_zones "
          "(existing rows defaulted to FALSE)")


if __name__ == "__main__":
    asyncio.run(main())
