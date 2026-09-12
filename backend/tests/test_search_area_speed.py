"""Where the search radius gets its speed — and why it refuses some readings.

The radius is speed x time, so the speed is half of the only number a search
party acts on. It can come from three places of very different worth, and the
endpoint reports which one it used, because "we watched them walking at this
pace" and "we assumed the population average" describe the same circle with
very different confidence.

The case that forced this: a phone reporting 0.15 m/s of standing-still jitter
was taken literally, and a patient missing for half an hour got a 262 m circle
instead of the 2,520 m the cautious default would have drawn.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.api.search_area import _MIN_TRUSTED_SPEED_MS, _choose_speed, get_search_area
from app.db import crud

pytestmark = pytest.mark.asyncio


class _Profile:
    """Stand-in for the ORM row — only one field is read."""

    def __init__(self, speed):
        self.avg_walking_speed_ms = speed


def _fix(speed, *, defaulted=False):
    return {"speed_ms": speed, "_meta": {"speed_defaulted": defaulted}}


def test_a_standing_still_reading_is_not_a_walking_pace():
    """0.15 m/s is GPS jitter. Believing it draws a circle far too small."""
    speed, source = _choose_speed(None, _fix(0.15), _Profile(1.1))
    assert (speed, source) == (1.1, "learned")


def test_the_learned_pace_beats_the_population_constant():
    speed, source = _choose_speed(None, _fix(None, defaulted=True), _Profile(0.8))
    assert (speed, source) == (0.8, "learned")


def test_a_real_walking_reading_wins_over_everything_stored():
    """The live fix is the only source that knows about today."""
    speed, source = _choose_speed(None, _fix(1.2), _Profile(0.8))
    assert (speed, source) == (1.2, "last_fix")


def test_a_fast_reading_is_kept_not_clamped():
    """The floor is deliberately one-sided.

    A reading above walking pace (running, or being driven) widens the circle,
    and a circle too wide costs searchers time. One too small costs them the
    patient, so only low readings are treated as suspect.
    """
    speed, source = _choose_speed(None, _fix(4.0), _Profile(0.8))
    assert (speed, source) == (4.0, "last_fix")
    assert 4.0 > _MIN_TRUSTED_SPEED_MS


def test_with_nothing_known_it_says_so():
    speed, source = _choose_speed(None, _fix(0.1), _Profile(None))
    assert source == "default"
    assert speed == pytest.approx(1.4)


async def test_the_endpoint_reports_which_speed_it_used(db_session):
    """End to end: a jittery last fix plus a learned pace, through the API."""
    db = db_session
    user = await crud.create_user(db, firebase_uid="speed_source_test", name="Pat",
                                  role="patient")
    await db.flush()
    pid = user.id

    # Old enough to count as missing (gps_gap_seconds defaults to 600 s), and
    # reporting the standing-still jitter that started all this.
    await crud.save_gps_point(
        db, pid, latitude=13.7563, longitude=100.5018, speed=0.15,
        recorded_at=datetime.now(timezone.utc) - timedelta(minutes=40),
    )
    await crud.upsert_behavioral_profile(db, pid, avg_walking_speed_ms=1.1)
    await db.commit()

    out = (await get_search_area(
        pid, last_lat=None, last_lng=None, last_speed_ms=None,
        last_direction_deg=None, time_missing_minutes=30, db=db, _=None,
    )).model_dump()
    await db.commit()

    assert out["status"] == "ok"
    assert out["speed_source"] == "learned"
    assert out["speed_ms_used"] == pytest.approx(1.1)
    # 1.1 m/s x 30 min — not the 270 m the raw 0.15 reading would have given.
    assert out["search_radius_meters"] == pytest.approx(1980.0, abs=1.0)
