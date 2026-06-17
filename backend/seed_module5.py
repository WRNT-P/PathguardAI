# pathguard/backend/seed_module5.py
"""Seed a mock behavioral profile so Module 5 can be tested before live GPS exists.

Inserts (idempotently) one patient + a known_places profile shaped exactly like
Module 1's output. routine_patterns is intentionally left unset, mirroring the
real pipeline and exercising Module 5's time-match fallback.

Run from the backend/ directory:
    python seed_module5.py
"""
import asyncio
import json

from app.db import crud
from app.db.database import AsyncSessionLocal, init_db

PATIENT_FIREBASE_UID = "seed_patient_module5"
PATIENT_NAME = "Seed Patient (Module 5)"

# Three places a few hundred metres apart so proximity ranking is visible.
# Home: most visited + longest stay. Market: mid. Park: least.
MOCK_PLACES = [
    {"cluster_id": 0, "latitude": 13.7563, "longitude": 100.5018,
     "visit_frequency": 120, "avg_stay_time": 600.0},   # Home
    {"cluster_id": 1, "latitude": 13.7590, "longitude": 100.5040,
     "visit_frequency": 40,  "avg_stay_time": 30.0},     # Market
    {"cluster_id": 2, "latitude": 13.7610, "longitude": 100.5005,
     "visit_frequency": 15,  "avg_stay_time": 90.0},     # Park
]


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        patient_id = await crud.get_user_id_by_firebase_uid(session, PATIENT_FIREBASE_UID)
        if patient_id is None:
            user = await crud.create_user(
                session,
                firebase_uid=PATIENT_FIREBASE_UID,
                name=PATIENT_NAME,
                role="patient",
            )
            patient_id = user.id

        await crud.upsert_behavioral_profile(
            session,
            patient_id=patient_id,
            known_places=json.dumps(MOCK_PLACES),
        )
        await session.commit()

    print(f"Seeded patient_id={patient_id} with {len(MOCK_PLACES)} known places.")
    print("Try (proximity favours Market):")
    print(f"  curl 'http://localhost:8000/api/recommendation/{patient_id}?lat=13.7589&lng=100.5041'")
    print("Or without location (frequency + familiarity only):")
    print(f"  curl 'http://localhost:8000/api/recommendation/{patient_id}'")


if __name__ == "__main__":
    asyncio.run(main())
