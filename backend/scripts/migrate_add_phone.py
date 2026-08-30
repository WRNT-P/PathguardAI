"""Idempotent migration: add users.phone.

The app's SOS contact screen has a Call button. It was wired on 2026-08-30 and
dials a number hardcoded in the Dart source, because ``users`` held no phone
number for anybody — the screen whose whole job is reaching a human being had
no way to ask the database who to reach.

Same reason ``migrate_add_severity_level`` exists: ``create_all`` adds missing
*tables* on boot and never missing *columns*, so a new column on ``users`` has
to be added by hand or it silently never appears on Neon.

Run once against the real database:

    python -m scripts.migrate_add_phone
"""
import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        # Nullable: every existing row predates the column, and inventing a
        # number for an emergency contact is worse than having none — a wrong
        # number on that screen fails at the exact moment it is needed.
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(32)"
        ))
    print("OK: users.phone present (existing rows left NULL)")


if __name__ == "__main__":
    asyncio.run(main())
