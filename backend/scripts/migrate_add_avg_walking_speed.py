"""Idempotent migration: add behavioral_profiles.avg_walking_speed_ms.

How fast this patient actually walks, learned from their own moving GPS fixes
by ``module1_behavior/behavior_pipeline.py``. Module 4 sizes a search area as
speed x time, and until now the only speed available to it was a constant.

Same reason the other ``migrate_add_*`` scripts exist: ``create_all`` adds
missing *tables* on boot and never missing *columns*, so a new column has to be
added by hand or every write to it 500s on Neon while the tests stay green.

Run once against the real database:

    python -m scripts.migrate_add_avg_walking_speed
"""
import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        # Nullable, no default. A patient whose history holds no walking fixes
        # yet has not told us their pace, and Module 4 already documents what it
        # does when it does not know — a default here would quietly outrank it.
        await conn.execute(text(
            "ALTER TABLE behavioral_profiles "
            "ADD COLUMN IF NOT EXISTS avg_walking_speed_ms DOUBLE PRECISION"
        ))
    print("OK: behavioral_profiles.avg_walking_speed_ms present "
          "(existing rows left NULL until the next training pass)")


if __name__ == "__main__":
    asyncio.run(main())
