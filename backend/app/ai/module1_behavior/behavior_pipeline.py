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
from app.ai.module1_behavior.place_clustering import cluster_places


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
    4. persist the learned places to the patient's behavioral profile

    Returns the clustered places (also stored on the profile).
    """
    records = await crud.get_gps_history(db, patient_id, days=days)
    if not records:
        return {"patient_id": patient_id, "places": [], "note": "no GPS history"}

    df = gps_history_to_dataframe(records)
    df = preprocess_gps(df)
    places = cluster_places(df)

    await crud.upsert_behavioral_profile(
        db,
        patient_id=patient_id,
        known_places=json.dumps(places),
        last_trained_at=datetime.now(timezone.utc),
    )

    return {"patient_id": patient_id, "places": places}
