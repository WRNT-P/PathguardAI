# pathguard/backend/app/ai/module1_behavior/behavior_pipeline.py
"""Connector between the PostgreSQL data layer and AI Module 1 (behavior).

This is the glue the ``data_collection.py`` TODO was waiting on. It reads a
patient's GPS history from PostgreSQL (written by ``services/gps_processor``),
runs Module 1's existing preprocess + place-clustering steps, and writes the
learned places back to the patient's behavioral profile.

DB in, DB out — the AI steps themselves (``preprocess_gps``, ``cluster_places``)
are untouched; this module only adapts their inputs/outputs to the data layer.
"""
import json
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.models import GPSData
from app.ai.module1_behavior.data_preprocessing import preprocess_gps
from app.ai.module1_behavior.known_places import decode, merge_learned
from app.ai.module1_behavior.place_clustering import cluster_places
from app.ai.module1_behavior.routine_patterns import build_routine_patterns

# A fix slower than this is somebody standing still with GPS noise around them;
# faster than this is a vehicle. Averaging either into "how fast do they walk"
# is what makes a search radius wrong in the direction that loses people.
_WALK_MIN_MS = 0.3
_WALK_MAX_MS = 2.5

# Relearn a patient's profile at most this often. One pass reads 30 days of
# GPS, Kalman-smooths it and runs DBSCAN, which is far heavier than a risk
# round — and what it learns moves on the scale of days, not minutes.
PROFILE_TRAIN_INTERVAL_S = 900


def average_walking_speed_ms(records: list[GPSData]) -> float | None:
    """Mean speed across this patient's own walking fixes, or None.

    None rather than a default: Module 4 already has a documented fallback for
    "we don't know", and a made-up number here would silently outrank it.
    """
    speeds = [
        r.speed for r in records
        if r.speed is not None and _WALK_MIN_MS <= r.speed <= _WALK_MAX_MS
    ]
    if not speeds:
        return None
    return round(sum(speeds) / len(speeds), 3)


def gps_history_to_dataframe(records: list[GPSData]) -> pd.DataFrame:
    """Convert ORM ``GPSData`` rows into the DataFrame shape Module 1 expects.

    Maps ``recorded_at`` -> ``timestamp`` and keeps the raw lat/lng/speed columns
    that ``preprocess_gps`` and ``cluster_places`` read.
    """
    return pd.DataFrame(
        {
            "latitude": [r.latitude for r in records],
            "longitude": [r.longitude for r in records],
            "speed": [r.speed for r in records],
            "timestamp": [r.recorded_at for r in records],
        }
    )


async def analyze_behavior(
    db: AsyncSession, patient_id: int, days: int = 30
) -> dict:
    """Run the full Module 1 pipeline for one patient: DB read -> learn -> DB write.

    1. read the last ``days`` of GPS history from PostgreSQL
    2. preprocess (clean + Kalman smooth)
    3. cluster frequent places (DBSCAN)
    4. merge with the caregiver's pins and persist to the behavioral profile

    Step 4 used to be a wholesale overwrite, which would have deleted every
    caregiver pin the first night this ran. It now keeps them, and rescales what
    was learned onto the same axes the pins use — see ``known_places``.

    Returns the merged place list (also stored on the profile).
    """
    records = await crud.get_gps_history(db, patient_id, days=days)
    if not records:
        return {"patient_id": patient_id, "places": [], "note": "no GPS history"}

    df = gps_history_to_dataframe(records) # แปลง list ของ GPS records (จาก database) ให้กลายเป็น pandas DataFrame (ตารางข้อมูล)
    df = preprocess_gps(df) # รับ DataFrame เข้าไป ทำความสะอาด (ตามที่ doc บอก: ลบ noise ด้วย Kalman Filter, normalize เวลา, แปลงหน่วยความเร็ว) แล้ว return DataFrame ที่สะอาดแล้ว ทับตัวแปรเดิม
    learned = cluster_places(df) # รับ DataFrame ที่สะอาดแล้ว ส่งเข้า clustering (DBSCAN) แล้วคืนค่าเป็น list of places (dict)

    # หมุดที่ผู้ดูแลปักไว้ต้องไม่หาย และค่าที่เรียนรู้มาต้องถูกปรับสเกลให้ตรงกันก่อนผสม
    profile = await crud.get_behavioral_profile(db, patient_id)
    places = merge_learned(decode(profile.known_places if profile else None), learned)

    # When is this patient usually where, and how fast do they walk. Both are
    # read off the same history this pass already loaded, and both are derived
    # from `places` above — so they are rebuilt here, in the same pass, rather
    # than left for a script somebody has to remember to run after every
    # change to the pins.
    routine = build_routine_patterns(
        [(r.latitude, r.longitude, r.recorded_at) for r in records], places
    )
    walking_speed = average_walking_speed_ms(records)

    await crud.upsert_behavioral_profile( # บันทึกผลลัพธ์กลับ database
        db,
        patient_id=patient_id,
        known_places=json.dumps(places, ensure_ascii=False), # places เป็น list ของ dict (Python object) ต้องแปลงเป็น string JSON ก่อนเก็บลง database
        routine_patterns=json.dumps(routine, ensure_ascii=False),
        avg_walking_speed_ms=walking_speed,
        last_trained_at=datetime.now(timezone.utc), # บันทึกเวลาปัจจุบัน (UTC timezone) ไว้
    )

    return {
        "patient_id": patient_id,
        "places": places,
        "routine_patterns": routine,
        "avg_walking_speed_ms": walking_speed,
    }
