"""The watchdog is the only thing that can notice a phone which stopped reporting.

Every other path to a ``gps_loss`` alert needs something the silence rules out:
``risk.py`` writes one but only runs on GPS ingest, where the gap it measures is
always ~0 because the reading it measures against is the one that just arrived;
``search_area.py`` writes one but only after a caregiver has already opened the
search by hand. So these tests cover the case the report describes and nothing
else could reach — the patient's phone dies and the family is told.

Four behaviours have to hold for an unattended loop to be safe to leave running:
it raises on silence, stays quiet on a live phone, never pages about a patient
who was never set up, and does not re-raise while the family has already been
told.

Seeded through crud rather than ``POST /api/gps`` on purpose: posting a stale
reading would run ``evaluate_risk``, which writes a ``gps_loss`` alert of its
own, and the test would then be measuring risk.py instead of the watchdog.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import crud
from app.services.gps_watchdog import scan_once

pytestmark = pytest.mark.asyncio

# The seeded ``gps_gap_seconds``. The code under test reads it from the rule KB;
# it is named here only to place each fixture either side of it.
GAP_S = 600


async def _patient(db, uid: str) -> int:
    user = await crud.create_user(db, firebase_uid=uid, name="P", role="patient")
    return user.id


async def _fix(db, patient_id: int, *, seconds_ago: float) -> None:
    """One GPS reading, that many seconds in the past."""
    await crud.save_gps_point(
        db,
        patient_id=patient_id,
        latitude=13.7563,
        longitude=100.5018,
        recorded_at=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
        speed=0.0,
    )


async def _gps_loss_alerts(db, patient_id: int) -> list:
    return await crud.get_unresolved_alerts_by_type(db, patient_id, "gps_loss")


async def test_silent_phone_raises_one_alert(db_session):
    """A patient last seen well past the threshold is reported once."""
    patient_id = await _patient(db_session, "silent")
    await _fix(db_session, patient_id, seconds_ago=GAP_S * 2)

    summary = await scan_once(db_session)

    assert summary == {"checked": 1, "lost": 1, "raised": 1}
    alerts = await _gps_loss_alerts(db_session, patient_id)
    assert len(alerts) == 1
    assert alerts[0].severity == "high"


async def test_alert_carries_the_last_known_position(db_session):
    """The caregiver's search starts from this, so it must not be empty."""
    patient_id = await _patient(db_session, "silent-where")
    await _fix(db_session, patient_id, seconds_ago=GAP_S * 2)

    await scan_once(db_session)

    alert = (await _gps_loss_alerts(db_session, patient_id))[0]
    assert alert.latitude == pytest.approx(13.7563)
    assert alert.longitude == pytest.approx(100.5018)


async def test_live_phone_raises_nothing(db_session):
    """A reading inside the threshold is not a gap."""
    patient_id = await _patient(db_session, "live")
    await _fix(db_session, patient_id, seconds_ago=30)

    summary = await scan_once(db_session)

    assert summary == {"checked": 1, "lost": 0, "raised": 0}
    assert await _gps_loss_alerts(db_session, patient_id) == []


async def test_patient_who_never_reported_is_not_missing(db_session):
    """A registered patient with no GPS at all must not page anybody.

    ``detect_gps_gap`` treats "no reading" as lost by design — unknown means
    caution once a search is already under way. Here that would mean every
    patient whose phone has not been paired yet wakes their family, so the
    watchdog skips them before asking.
    """
    patient_id = await _patient(db_session, "never-reported")

    summary = await scan_once(db_session)

    assert summary == {"checked": 0, "lost": 0, "raised": 0}
    assert await _gps_loss_alerts(db_session, patient_id) == []


async def test_open_alert_is_not_raised_again(db_session):
    """A phone that stays off must not write one row a minute, for ever.

    The condition holds on every scan; the alert row is what remembers that the
    family has been told. risk.py resolves it when GPS comes back.
    """
    patient_id = await _patient(db_session, "still-silent")
    await _fix(db_session, patient_id, seconds_ago=GAP_S * 2)

    first = await scan_once(db_session)
    second = await scan_once(db_session)

    assert first["raised"] == 1
    assert second["lost"] == 1, "still lost — it just must not re-raise"
    assert second["raised"] == 0
    assert len(await _gps_loss_alerts(db_session, patient_id)) == 1


async def test_caregivers_are_not_scanned_as_patients(db_session):
    """Only ``role == 'patient'`` rows are candidates."""
    await crud.create_user(
        db_session, firebase_uid="cg", name="C", role="caregiver")

    summary = await scan_once(db_session)

    assert summary == {"checked": 0, "lost": 0, "raised": 0}


async def test_one_silent_patient_among_several(db_session):
    """The scan reports the quiet phone without touching the live ones."""
    quiet = await _patient(db_session, "quiet-one")
    await _fix(db_session, quiet, seconds_ago=GAP_S * 3)
    for i in range(2):
        live = await _patient(db_session, f"live-{i}")
        await _fix(db_session, live, seconds_ago=10)

    summary = await scan_once(db_session)

    assert summary == {"checked": 3, "lost": 1, "raised": 1}
    assert len(await _gps_loss_alerts(db_session, quiet)) == 1
