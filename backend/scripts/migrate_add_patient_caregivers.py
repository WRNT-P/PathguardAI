"""Idempotent migration: the whole multi-caregiver schema.

Two things, in one script because they are one feature and asking the user to
run two commands in the right order is a way to have one of them skipped:

1. ``patient_caregivers``, and the data out of ``users.caregiver_id``;
2. ``users.last_latitude`` / ``last_longitude`` / ``location_updated_at`` — where
   a caregiver is, without which "ranked by distance" cannot be computed;
3. ``caregiver_invites`` — the only way a *second* caregiver can be added.
   ``create_all`` would make this one by itself, but it is listed here so one
   command leaves the database in a state the whole feature works against.


``users.caregiver_id`` was one nullable FK, so a patient had at most one
caregiver. The report has always promised "alert every caregiver, ranked by
distance, tap to claim", and on 2026-08-28 the app side confirmed they want it
and are waiting on this table.

**Why this script has to exist.** ``app/db/database.py:init_db`` calls
``Base.metadata.create_all``, which creates missing *tables* — so
``patient_caregivers`` would appear on the next boot by itself. What it will
never do is *move the rows*: every existing patient would boot with an empty
caregiver set, which is not a crash but something worse. Nobody would be
notified about them, and their caregiver would get a 403 reading their own
patient's alerts, both silently.

**users.caregiver_id is deliberately not dropped.** Until this has run against
Neon and been eyeballed, that column is the only copy of who looks after whom.
It is already invisible to the application — the ORM model stopped declaring it
on 2026-08-28 — so nothing reads it by accident, and an extra column costs
nothing. Dropping it is a separate, later, one-line migration once the new
table has carried a pilot.

Run against Neon:

    python -m scripts.migrate_add_patient_caregivers
"""
import asyncio

from sqlalchemy import text

from app.db.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS patient_caregivers (
                id            BIGSERIAL PRIMARY KEY,
                patient_id    BIGINT NOT NULL REFERENCES users(id),
                caregiver_id  BIGINT NOT NULL REFERENCES users(id),
                is_primary    BOOLEAN NOT NULL DEFAULT FALSE,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_patient_caregiver UNIQUE (patient_id, caregiver_id)
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_patient_caregivers_patient "
            "ON patient_caregivers (patient_id)"))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_patient_caregivers_caregiver "
            "ON patient_caregivers (caregiver_id)"))

        # Skip the copy on a database that never had the old column — a fresh
        # Neon created from the current models, or a re-run after the eventual
        # DROP COLUMN. Not an error either way.
        has_old_column = await conn.scalar(text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'caregiver_id'
        """))

        moved = 0
        if has_old_column:
            # Whoever was in the old column created the patient, so they are the
            # primary. ON CONFLICT makes the whole script re-runnable: a second
            # run over a table that already holds these links changes nothing,
            # and in particular does not demote a caregiver added since.
            result = await conn.execute(text("""
                INSERT INTO patient_caregivers
                    (patient_id, caregiver_id, is_primary)
                SELECT id, caregiver_id, TRUE
                FROM users
                WHERE caregiver_id IS NOT NULL
                ON CONFLICT ON CONSTRAINT uq_patient_caregiver DO NOTHING
            """))
            moved = result.rowcount

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS caregiver_invites (
                id          BIGSERIAL PRIMARY KEY,
                code        VARCHAR(16) NOT NULL UNIQUE,
                patient_id  BIGINT NOT NULL REFERENCES users(id),
                invited_by  BIGINT REFERENCES users(id),
                expires_at  TIMESTAMPTZ NOT NULL,
                used_at     TIMESTAMPTZ,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_caregiver_invite_patient "
            "ON caregiver_invites (patient_id)"))

        # Where a caregiver last was. Nullable and never backfilled: inventing a
        # position for somebody would put them at the top of a distance ranking
        # for a patient they may be nowhere near. Latest only — no history table
        # on purpose, see the comment on the columns in models.py.
        for column, ddl_type in (
            ("last_latitude", "DOUBLE PRECISION"),
            ("last_longitude", "DOUBLE PRECISION"),
            ("location_updated_at", "TIMESTAMPTZ"),
        ):
            await conn.execute(text(
                f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {column} {ddl_type}"
            ))

        total = await conn.scalar(text("SELECT COUNT(*) FROM patient_caregivers"))
        orphans = await conn.scalar(text("""
            SELECT COUNT(*) FROM users u
            WHERE u.role = 'patient'
              AND NOT EXISTS (SELECT 1 FROM patient_caregivers pc
                              WHERE pc.patient_id = u.id)
        """))

    print(f"OK: patient_caregivers present, {moved} link(s) copied this run, "
          f"{total} total")
    print("OK: users.last_latitude / last_longitude / location_updated_at present "
          "(existing rows left NULL)")
    print("OK: caregiver_invites present")
    if orphans:
        # Not fatal and not necessarily wrong — a patient registered through
        # /api/register never had a caregiver. Printed because if it is not zero
        # on a database that had a working pilot, the copy above missed someone
        # and nobody will be notified about them.
        print(f"WARNING: {orphans} patient(s) have no caregiver linked at all — "
              f"nobody will be pushed to for them. Check before flipping auth.")
    if not has_old_column:
        print("NOTE: users.caregiver_id is already gone; nothing to copy.")


if __name__ == "__main__":
    asyncio.run(main())
