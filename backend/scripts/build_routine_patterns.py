"""Fill ``behavioral_profiles.routine_patterns`` from a patient's GPS history.

The column has existed since the schema was written and nothing ever wrote it,
so Module 5 ranked places on three of its four factors — ``time_match`` was
pinned at weight 0.0 and hardcoded to zero. This is its writer.

**This is not the nightly clustering job, and running it does not start one.**
``analyze_behavior`` stays uncalled (L3-0/L3-2): DBSCAN over raw fixes invents
places, and caregiver pins are the design. What this reads is those same pins —
it only learns *which hours* the patient tends to be at each. It cannot create,
move or rename a place, and with no pins on file it writes nothing at all.

Run it against Neon whenever a patient has accumulated history worth learning
from; there is no scheduler and it does not need one, because a routine that is
a week stale is still a routine:

    python -m scripts.build_routine_patterns                 # every patient with pins
    python -m scripts.build_routine_patterns --patient 42
    python -m scripts.build_routine_patterns --days 14 --dry-run
"""
import argparse
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.ai.module1_behavior.known_places import decode as decode_places
from app.ai.module1_behavior.routine_patterns import build_routine_patterns
from app.db import crud
from app.db.database import AsyncSessionLocal
from app.db.models import BehavioralProfile


async def _patient_ids(db, requested: int | None) -> list[int]:
    if requested is not None:
        return [requested]
    result = await db.execute(select(BehavioralProfile.patient_id))
    return list(result.scalars().all())


async def build_for(db, patient_id: int, days: int, dry_run: bool) -> str:
    profile = await crud.get_behavioral_profile(db, patient_id)
    places = decode_places(profile.known_places if profile else None)
    if not places:
        return f"patient {patient_id}: no pins on file — nothing to learn against"

    history = await crud.get_gps_history(db, patient_id, days=days)
    if not history:
        return f"patient {patient_id}: no GPS in the last {days} days"

    patterns = build_routine_patterns(
        [(p.latitude, p.longitude, p.recorded_at) for p in history], places
    )
    hours = len({p["hour"] for p in patterns})
    summary = (
        f"patient {patient_id}: {len(history)} fixes over {days} d, "
        f"{len(places)} places -> {len(patterns)} patterns across {hours} hours"
    )
    if dry_run:
        return summary + "  (dry run, nothing written)"

    await crud.upsert_behavioral_profile(
        db, patient_id,
        routine_patterns=json.dumps(patterns, ensure_ascii=False),
        last_trained_at=datetime.now(timezone.utc),
    )
    await db.commit()
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patient", type=int, default=None,
                        help="one patient id; default is every patient with a profile")
    parser.add_argument("--days", type=int, default=30,
                        help="how much history to read (default 30)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written and write nothing")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        ids = await _patient_ids(db, args.patient)
        if not ids:
            print("no behavioral profiles found — pin a place first")
            return
        for patient_id in ids:
            print(await build_for(db, patient_id, args.days, args.dry_run))


if __name__ == "__main__":
    asyncio.run(main())
