"""The access list the Realtime Database rules read.

Rules cannot reach Postgres, so ``access/{patient_id}/{firebase_uid}`` is the
only thing standing between "this family's chat" and "any signed-in account".
Getting the list wrong fails in two directions and neither announces itself: a
missing uid locks a family out of their own room, and a stray one hands a
stranger the room. Hence tests for a list.
"""
import pytest

from app.db import crud
from app.services import firebase

pytestmark = pytest.mark.asyncio


async def _patient_with_caregiver(db, *, patient_uid, caregiver_uid):
    caregiver = await crud.create_user(
        db, firebase_uid=caregiver_uid, name="cg", role="caregiver")
    await db.flush()
    patient = await crud.create_user(
        db, firebase_uid=patient_uid, name="pt", role="patient",
        caregiver_id=caregiver.id)
    await db.flush()
    return patient, caregiver


async def test_the_list_is_the_patient_and_their_caregivers(db_session):
    patient, caregiver = await _patient_with_caregiver(
        db_session, patient_uid="pathguard:pt", caregiver_uid="cg-uid")

    uids = await crud.get_patient_access_uids(db_session, patient.id)

    assert set(uids) == {"pathguard:pt", "cg-uid"}, (
        "the patient needs their own row — they write the trip requests"
    )
    assert caregiver.firebase_uid in uids


async def test_a_second_caregiver_joins_the_list(db_session):
    patient, _ = await _patient_with_caregiver(
        db_session, patient_uid="pathguard:pt2", caregiver_uid="cg-a")
    second = await crud.create_user(
        db_session, firebase_uid="cg-b", name="cg2", role="caregiver")
    await db_session.flush()
    await crud.link_caregiver(db_session, patient.id, second.id)

    uids = await crud.get_patient_access_uids(db_session, patient.id)

    assert set(uids) == {"pathguard:pt2", "cg-a", "cg-b"}


async def test_another_familys_caregiver_is_not_on_the_list(db_session):
    """The whole point. A caregiver looking after one patient must not appear
    on another patient's list, or the rules hand them a room that is not
    theirs."""
    mine, _ = await _patient_with_caregiver(
        db_session, patient_uid="pathguard:mine", caregiver_uid="cg-mine")
    theirs, _ = await _patient_with_caregiver(
        db_session, patient_uid="pathguard:theirs", caregiver_uid="cg-theirs")

    assert "cg-theirs" not in await crud.get_patient_access_uids(db_session, mine.id)
    assert "cg-mine" not in await crud.get_patient_access_uids(db_session, theirs.id)


async def test_a_patient_with_no_caregiver_yet_still_gets_themselves(db_session):
    patient = await crud.create_user(
        db_session, firebase_uid="pathguard:alone", name="pt", role="patient")
    await db_session.flush()

    assert await crud.get_patient_access_uids(db_session, patient.id) == [
        "pathguard:alone"]


async def test_a_sync_that_cannot_reach_firebase_does_not_fail_the_request(
        db_session, monkeypatch):
    """Best-effort on purpose, and this is the line that says so.

    Firebase is not initialised in tests, so this already exercises the real
    failure — but pinning it means nobody can make the sync raise later and
    take pairing down with it. A dropped sync costs a chat that opens on the
    next one; a failed pairing costs the patient their phone.
    """
    patient, _ = await _patient_with_caregiver(
        db_session, patient_uid="pathguard:x", caregiver_uid="cg-x")

    def explode(*args, **kwargs):
        raise RuntimeError("firebase is down")

    monkeypatch.setattr(firebase, "set_patient_access", explode)

    await firebase.sync_patient_access(db_session, patient.id)  # must not raise


async def test_the_sync_sends_exactly_the_list_crud_reports(db_session, monkeypatch):
    patient, _ = await _patient_with_caregiver(
        db_session, patient_uid="pathguard:y", caregiver_uid="cg-y")

    sent = {}
    monkeypatch.setattr(
        firebase, "set_patient_access",
        lambda pid, uids: sent.update(patient_id=pid, uids=set(uids)))

    await firebase.sync_patient_access(db_session, patient.id)

    assert sent == {"patient_id": patient.id, "uids": {"pathguard:y", "cg-y"}}
