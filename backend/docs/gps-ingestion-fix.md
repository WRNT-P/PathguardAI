# Handoff: repoint `/api/gps` to the real ingestion path

**For:** the GPS endpoint owner
**From:** database side
**Status:** open — blocks all AI modules from receiving data

## Problem

`app/api/gps.py` persists incoming GPS to an **in-memory list**, not PostgreSQL:

```python
# app/api/gps.py:5,25
from app.ai.module1_behavior.data_collection import save_gps_data
...
save_gps_data(data.model_dump())   # appends to a Python list, lost on restart
```

`data_collection.py` is a placeholder (`gps_storage = []`) with a TODO to switch
to the DB once `get_db()` existed. `get_db()` now exists, but the endpoint was
never repointed.

## Impact

- Nothing reaches the `gps_data` table (it is currently empty in the live DB).
- Module 1 (behavior) — and modules 2–5 — read history via
  `crud.get_gps_history()`, so with an empty table they have nothing to analyze.
- The Module 1 ↔ DB connector (`app/ai/module1_behavior/behavior_pipeline.py`)
  is built and verified end-to-end against the live DB, but it can only produce
  results once real GPS history starts landing in Postgres.

## The fix

A proper single-writer ingestion path already exists:
`app/services/gps_processor.py → process_gps_point()`. It Kalman-smooths,
persists raw + smoothed to PostgreSQL, and pushes the live position to Firebase.
Repoint the endpoint to it.

```python
# app/api/gps.py (sketch)
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.gps_data import GPSDataCreate
from app.services.gps_processor import process_gps_point

router = APIRouter()

@router.post("/api/gps", summary="รับข้อมูล GPS จาก Flutter app")
async def receive_gps(data: GPSDataCreate, db: AsyncSession = Depends(get_db)):
    point = await process_gps_point(db, data)
    return {"status": "success", "patient_id": data.patient_id, "id": point.id}
```

## Schema mismatches to reconcile (current `GPSData` request model vs `GPSDataCreate`)

The endpoint's current Pydantic model doesn't match what `process_gps_point`
expects (`app/models/gps_data.py::GPSDataCreate`):

| Field        | Current `/api/gps` model | `GPSDataCreate` (needed) |
|--------------|--------------------------|--------------------------|
| `patient_id` | `str`                    | `int` (FK → `users.id`)  |
| time field   | `timestamp: str`         | `recorded_at: datetime`  |
| `accuracy`   | absent                   | `float \| None`          |
| `altitude`   | absent                   | `float \| None`          |
| `direction`, `device_motion` | present  | not used by ingestion    |

Decide with the Flutter side whether the app sends `patient_id` as an int and an
ISO `recorded_at`, or whether the endpoint should translate. Simplest is to
accept `GPSDataCreate` directly (as in the sketch) and update the Flutter payload.

## Once fixed

The Module 1 pipeline runs with no further DB work:

```python
from app.ai.module1_behavior.behavior_pipeline import analyze_behavior
result = await analyze_behavior(db, patient_id)   # reads history, clusters, writes profile
```

## Unrelated, but noticed (Module 1 owner)

`preprocess_gps`'s Kalman params (`R=1e-3 ≫ Q=1e-5`) smooth across location jumps,
so two places ~1 km apart collapse into one cluster. Worth tuning, but separate
from this ingestion fix.

## Team Decisions

Resolutions to the five open questions. Q1, Q2, Q4 touch the DB schema and are
settled on the DB side. Q3 and Q5 still need the GPS owner + Flutter dev to
confirm on their end.

1. **`patient_id` → int (settled, DB side).**
   `gps_data.patient_id` stays an int FK to `users.id` — schema unchanged. The
   Flutter/Firebase string ID is stored separately in `users.firebase_uid`. The
   endpoint resolves `firebase_uid → users.id` before writing GPS. (Ties into Q2.)

2. **`users` table / row creation → DB side.**
   DB side creates the `users` table (if not present) and adds a **register
   endpoint** that creates the user row — storing `firebase_uid` at the same
   time — so the FK target exists before any GPS arrives.

3. **timestamp → app is source of truth, server fallback (needs GPS + Flutter
   confirm).** Parse the app's ISO timestamp into `recorded_at`; if missing, the
   server stamps `datetime.now(timezone.utc)`. All timestamps stored as UTC —
   Flutter must send UTC ISO strings (ending in `Z`).

4. **`direction` / `device_motion` → add columns (settled, DB side).**
   Store them rather than drop — Module 2 (Wandering Detection) needs
   `direction`. Adding the columns now avoids a later migration.

5. **Ownership of the `api/gps.py` repoint → GPS owner (needs GPS confirm).**
   It's their file, so they pick it up. DB side can step in if something's
   blocking, but only after telling them first and explaining the change.
