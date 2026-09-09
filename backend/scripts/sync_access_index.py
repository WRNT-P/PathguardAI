"""Rebuild the Realtime Database's access index for every patient.

    python -m scripts.sync_access_index

The database rules answer "may this account open this family's chat" by
looking up ``access/{patient_id}/{firebase_uid}``, which the backend writes
whenever a link is made. Patients linked before that existed have no entry,
so **this has to be run once before deploying the scoped rules** or every
existing family is locked out of their own room.

Safe to re-run at any time: it writes each patient's whole node from Postgres,
so it also repairs anything a dropped best-effort sync left behind.
"""
import asyncio
import logging

from sqlalchemy import select

from app.db import crud
from app.db.database import AsyncSessionLocal, init_firebase
from app.db.models import User
from app.services import firebase

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("sync_access_index")


async def main() -> None:
    init_firebase()
    async with AsyncSessionLocal() as db:
        patient_ids = (
            await db.execute(select(User.id).where(User.role == "patient"))
        ).scalars().all()

        if not patient_ids:
            log.info("no patients — nothing to sync")
            return

        for patient_id in patient_ids:
            uids = await crud.get_patient_access_uids(db, patient_id)
            firebase.set_patient_access(patient_id, uids)
            log.info("patient %s -> %d account(s) granted", patient_id, len(uids))

    log.info("done — %d patient(s) synced", len(patient_ids))


if __name__ == "__main__":
    asyncio.run(main())
