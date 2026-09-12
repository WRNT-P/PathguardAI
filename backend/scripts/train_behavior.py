"""Run Module 1's learning pass now, instead of waiting for ingestion to do it.

Ingestion already trains every patient on its own (``gps.py::
_train_profile_after_ingest``), but at most once every 15 minutes each — right
for a phone reporting all day, too slow to stand in front of an audience and
wait for. This runs the same ``analyze_behavior`` immediately and prints what
it saw, so a demo can show the learning rather than describe it.

**It writes to the profile.** ``--dry-run`` reports what would be learned and
rolls back instead, which is also the honest way to check a patient before
committing to what the app will start treating as familiar.

What gets learned, and why so little of it (see ``ai/module1_behavior/
trip_learning.py``): only places the patient chose in the app, walked to,
arrived at, stayed at, and returned to. Standing somewhere twice is not enough,
because that is also what getting lost in the same place twice looks like — so
a patient with plenty of GPS and no completed trips learns nothing here, and
that is the system working.

    python -m scripts.train_behavior --patient 57
    python -m scripts.train_behavior --patient 57 --dry-run
    python -m scripts.train_behavior                      # every patient with GPS
"""
import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone

# Before importing app.db.database: the engine reads DEBUG at import time and
# turns on SQL echo, which buries this script's own output.
os.environ["DEBUG"] = ""

from sqlalchemy import select  # noqa: E402

from app.ai.module1_behavior.behavior_pipeline import analyze_behavior  # noqa: E402
from app.ai.module1_behavior.trip_learning import (  # noqa: E402
    MIN_DISTINCT_DAYS, MIN_DWELL_S, MIN_VISITS, qualifying_visits, summarise,
)
from app.db import crud  # noqa: E402
from app.db.database import AsyncSessionLocal  # noqa: E402
from app.db.models import GPSData  # noqa: E402


async def simulate_trips(db, patient_id: int, lat: float, lng: float,
                         name: str, *, visits: int, dwell_minutes: int) -> None:
    """Write the rows a completed navigated trip would have left behind.

    For rehearsing the demo when nobody can walk the emulator to a real
    destination and stand there. Every row is flagged ``synthetic_injected``,
    the same marker ``inject_wandering.py`` uses, so invented history stays
    distinguishable from a patient's own — this writes to the real database.

    Each visit is one day apart so it satisfies the distinct-days rule even
    when that is dialled back up to its real value.
    """
    now = datetime.now(timezone.utc)
    for visit in range(visits):
        arrived_at = now - timedelta(days=visits - 1 - visit, minutes=dwell_minutes + 1)
        for minute in range(dwell_minutes + 1):
            point = await crud.save_gps_point(
                db, patient_id,
                latitude=lat + (minute % 3) * 1e-6,
                longitude=lng + (minute % 2) * 1e-6,
                speed=1.1,
                recorded_at=arrived_at + timedelta(minutes=minute),
            )
            point.synthetic_injected = True
        alert = await crud.save_alert(
            db, patient_id,
            alert_type="trip_arrived", severity="low",
            message=f"ถึง {name} แล้ว",
            latitude=lat, longitude=lng, destination_name=name,
        )
        alert.created_at = arrived_at
        alert.synthetic_injected = True
        await db.flush()
    print(f"  simulated {visits} completed trip(s) to {name}, "
          f"{dwell_minutes} min each (flagged synthetic)")


async def _patient_ids(db, requested: int | None) -> list[int]:
    if requested is not None:
        return [requested]
    result = await db.execute(select(GPSData.patient_id).distinct())
    return sorted(result.scalars().all())


async def train_one(db, patient_id: int, days: int) -> None:
    """Train one patient, narrating the steps that decide the outcome."""
    history = await crud.get_gps_history(db, patient_id, days=days)
    arrivals = await crud.get_trip_arrivals(db, patient_id, days=days)
    visits = qualifying_visits(arrivals, history)

    print(f"\npatient {patient_id}")
    print(f"  GPS fixes in {days} days : {len(history)}")
    print(f"  completed trips          : {len(arrivals)}")
    print(f"  stayed >= {MIN_DWELL_S / 60:.0f} min        : {len(visits)}"
          f"   (need {MIN_VISITS} at one place, on {MIN_DISTINCT_DAYS} day(s))")

    if arrivals and not visits:
        print("  → arrived but never stayed long enough. Nothing learned, and "
              "nothing wrong: a place they pass through is not a place they go.")

    result = await analyze_behavior(db, patient_id, days=days)
    pinned = [p for p in result["places"] if p.get("source") not in
              ("learned", "learned_trip")]

    print(f"  learned from trips       : "
          f"{summarise(result.get('learned_from_trips') or [])}")
    print(f"  caregiver pins kept      : {len(pinned)}")
    print(f"  routine hours            : {len(result.get('routine_patterns') or [])}")
    speed = result.get("avg_walking_speed_ms")
    print(f"  walking speed            : "
          f"{f'{speed} m/s' if speed else 'not enough moving fixes yet'}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patient", type=int, help="one patient id (default: all)")
    parser.add_argument("--days", type=int, default=30, help="history window")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be learned, then roll back")
    parser.add_argument("--simulate-arrival", nargs=3, metavar=("LAT", "LNG", "NAME"),
                        help="invent completed trips to this place first "
                             "(demo rehearsal; rows are flagged synthetic)")
    parser.add_argument("--simulate-visits", type=int, default=2)
    parser.add_argument("--simulate-dwell-minutes", type=int, default=20)
    args = parser.parse_args()

    if args.simulate_arrival and args.patient is None:
        parser.error("--simulate-arrival needs --patient: it writes history")

    async with AsyncSessionLocal() as db:
        ids = await _patient_ids(db, args.patient)
        if not ids:
            print("no patients with GPS history")
            return
        if args.simulate_arrival:
            lat, lng, name = args.simulate_arrival
            print(f"patient {args.patient}")
            await simulate_trips(db, args.patient, float(lat), float(lng), name,
                                 visits=args.simulate_visits,
                                 dwell_minutes=args.simulate_dwell_minutes)
        for patient_id in ids:
            await train_one(db, patient_id, args.days)
        if args.dry_run:
            await db.rollback()
            print("\n(dry run — nothing written)")
        else:
            await db.commit()
            print(f"\nwrote {len(ids)} profile(s)")


if __name__ == "__main__":
    asyncio.run(main())
