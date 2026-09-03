"""Idempotent migration: add users.is_available.

The patient's SOS contact screen showed ว่าง/ไม่ว่าง for each caregiver off a
value hardcoded in the Dart source — a person in trouble being told who to call
by a constant. This is the column behind it.

Same reason ``migrate_add_phone`` and ``migrate_add_severity_level`` exist:
``create_all`` adds missing *tables* on boot and never missing *columns*, so a
new column on ``users`` has to be added by hand or it silently never appears on
Neon, and every call to the endpoint 500s on a database the tests never see.

Run once against the real database:

    python -m scripts.migrate_add_is_available
"""
import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        # Nullable with NO default, deliberately. Every existing caregiver
        # predates the column and has never answered the question, and the two
        # defaults on offer are both a lie told on their behalf: TRUE volunteers
        # somebody who may be asleep, FALSE hides somebody who would have come.
        # NULL is the honest third state and the app renders it as unknown.
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_available BOOLEAN"
        ))
    print("OK: users.is_available present (existing rows left NULL = never answered)")


if __name__ == "__main__":
    asyncio.run(main())
