"""Erase one person from the database — the other half of "you can withdraw".

Written 2026-08-29, because a rehearsal found that we could not. The plan's D5
promises a family that everything can be deleted when the trial ends. Reading
the live route table, the API has **31 routes and exactly two DELETEs**: release
a claim, and deactivate a danger zone (a soft one at that). ``crud.py`` had a
single ``delete_`` function and it was for a device token. Nothing could remove
a patient, their track, their alerts or their profile. The promise had nothing
behind it, and the moment we would have discovered that is the moment a family
asks — which is the worst possible moment.

**Why a script and not ``DELETE /api/patients/{id}``.** Deleting a participant
is something a person does once, at the end of a trial. It is not a feature of
the app. The reasoning already recorded for ``danger_zones`` applies harder
here: every endpoint's guard is "is signed in", and in a prototype whose account
holders are one family and this team, "signed in" does not mean "in this house".
A permanent, authenticated way to erase a dementia patient's entire history is a
loaded gun left on the table for the benefit of an action nobody performs from
a phone.

**Deletes, never anonymises.** Withdrawal has to mean the rows are gone, because
a family cannot audit a flag.

Rows in *other* people's records that merely point at this user — who claimed
their alert, who decided their trip request, who invited them — are set to NULL,
never deleted. This person leaving must not delete another family's alert.

    python -m scripts.delete_patient --user-id 42            # dry run, default
    python -m scripts.delete_patient --user-id 42 --confirm  # actually delete

There is no ``--all``. One id per run, on purpose.
"""
import argparse
import asyncio
import sys

from sqlalchemy import text

from app.db.database import engine

# Child rows that belong to this person and go with them, in FK-safe order.
# push_notifications precedes alerts: it references alerts.id, and a push about
# this patient's alert carries this patient's id, so the first delete clears the
# way for the second.
OWNED = [
    ("push_notifications", "patient_id"),
    ("alerts", "patient_id"),
    ("gps_data", "patient_id"),
    ("risk_scores", "patient_id"),
    ("behavioral_profiles", "patient_id"),
    ("pairing_codes", "patient_id"),
    ("trip_requests", "patient_id"),
    ("caregiver_invites", "patient_id"),
    ("patient_caregivers", "patient_id"),
    ("patient_caregivers", "caregiver_id"),
    ("device_tokens", "user_id"),
]

# Somebody else's row that names this person. Cleared, not removed — see above.
# The third item is the companion timestamp that has to go with the name, and it
# is here because the first run of this script left it behind: the alert came
# back ``claimed_by: null`` with ``claimed_at`` still set, which the app renders
# as "somebody took this at 08:01" with nobody's name under it. A half-cleared
# claim is worse than either state — it tells a family help is coming and
# refuses to say from whom.
REFERENCED = [
    ("alerts", "claimed_by", "claimed_at"),
    ("trip_requests", "decided_by", "decided_at"),
    ("caregiver_invites", "invited_by", None),
]

# A rehearsal account has a handful of points. Anything on this scale is a real
# dataset — patient 13 carries the 10,389 GeoLife fixes every measurement in
# this project rests on, and losing it silently would invalidate the report.
BIG_TRACK = 1_000


async def counts(conn, user_id: int) -> tuple[dict, dict]:
    owned, referenced = {}, {}
    for table, column in OWNED:
        n = await conn.scalar(
            text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :uid"),
            {"uid": user_id})
        if n:
            owned[f"{table}.{column}"] = n
    for table, column, _paired in REFERENCED:
        n = await conn.scalar(
            text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :uid"),
            {"uid": user_id})
        if n:
            referenced[f"{table}.{column}"] = n
    return owned, referenced


async def main(user_id: int, confirm: bool) -> None:
    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT id, name, role, created_at FROM users WHERE id = :uid"),
            {"uid": user_id})).first()
        if row is None:
            print(f"no user with id {user_id} — nothing to do")
            return

        print(f"user {row.id}: {row.name!r} · role={row.role} · created {row.created_at}")
        owned, referenced = await counts(conn, user_id)

        print("\nwill DELETE:")
        for key, n in owned.items():
            print(f"  {key:38s} {n:>7,}")
        print(f"  {'users.id':38s} {1:>7,}")
        if referenced:
            print("\nwill SET NULL (rows belonging to somebody else):")
            for key, n in referenced.items():
                print(f"  {key:38s} {n:>7,}")

        track = owned.get("gps_data.patient_id", 0)
        if track >= BIG_TRACK:
            print(f"\n*** STOP: {track:,} GPS points. That is a real dataset, not a "
                  f"rehearsal account. Delete it by hand if you truly mean to. ***")
            return

        if not confirm:
            print("\ndry run — nothing was changed. Re-run with --confirm to delete.")
            return

        for table, column, paired in REFERENCED:
            also = f", {paired} = NULL" if paired else ""
            await conn.execute(
                text(f"UPDATE {table} SET {column} = NULL{also} "
                     f"WHERE {column} = :uid"),
                {"uid": user_id})
        for table, column in OWNED:
            await conn.execute(
                text(f"DELETE FROM {table} WHERE {column} = :uid"), {"uid": user_id})
        await conn.execute(
            text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})

    # Fresh connection, so the check reads what was committed rather than what
    # the deleting transaction believed.
    async with engine.begin() as conn:
        left_owned, left_referenced = await counts(conn, user_id)
        still_there = await conn.scalar(
            text("SELECT COUNT(*) FROM users WHERE id = :uid"), {"uid": user_id})
        residue = {**left_owned, **left_referenced}
        if residue or still_there:
            print(f"\nINCOMPLETE — still present: {residue} users={still_there}")
        else:
            print(f"\nverified: user {user_id} leaves no row in any table")


if __name__ == "__main__":
    # The first thing this prints is the person's name, and every name in this
    # project is Thai. A Windows console defaults to cp1252 and raises on it,
    # so without this the script dies on its own confirmation line — found by
    # running it, on the first try, before it had ever deleted anything.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):          # not a real console
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--confirm", action="store_true",
                        help="actually delete; without it this only reports")
    args = parser.parse_args()
    asyncio.run(main(args.user_id, args.confirm))
