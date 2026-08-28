"""Idempotent migration: ``alerts.claimed_by`` / ``alerts.claimed_at``.

"I'll go and get them" (report C-2). Two columns on the existing alerts row —
which caregiver is on their way, and since when.

**Why this script has to exist.** ``init_db`` calls ``create_all``, which adds
missing *tables* and never missing *columns*. ``alerts`` already exists on Neon
with 88 rows in it, so these two columns can only arrive through ALTER TABLE.
Without them every claim request fails with an UndefinedColumn error at the
moment a family is trying to say who is going.

Both are nullable with no default and nothing is backfilled: an unclaimed alert
is exactly what every existing row is, and inventing a claimer would tell a
family that somebody is already on their way to a patient who was never
collected.

Run against Neon:

    python -m scripts.migrate_add_alert_claim
"""
import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS "
            "claimed_by BIGINT REFERENCES users(id)"))
        await conn.execute(text(
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS "
            "claimed_at TIMESTAMPTZ"))

        present = await conn.scalar(text("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name = 'alerts'
              AND column_name IN ('claimed_by', 'claimed_at')
        """))
        claimed = await conn.scalar(text(
            "SELECT COUNT(*) FROM alerts WHERE claimed_by IS NOT NULL"))
        total = await conn.scalar(text("SELECT COUNT(*) FROM alerts"))

    if present != 2:
        raise SystemExit(
            f"FAILED: expected both columns on alerts, found {present}")
    print("OK: alerts.claimed_by / claimed_at present")
    print(f"OK: {claimed} of {total} alert(s) claimed "
          "(existing rows are left unclaimed on purpose)")


if __name__ == "__main__":
    asyncio.run(main())
