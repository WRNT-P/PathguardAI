"""L3-4 — the writer ``routine_patterns`` never had.

Module 5 has been ranking places on three of its four factors since it was
written: ``time_match`` was weight 0.0 and hardcoded to zero because nothing
filled the column. These cover the builder and the one seam that can break it
silently.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.module1_behavior.routine_patterns import (
    LOCAL_UTC_OFFSET_HOURS, build_routine_patterns, decode, local_hour,
    probability_at,
)
from app.ai.module5_recommend.recommendation_generation import score_place
from app.ai.module5_recommend.user_context_analysis import (
    UserContext, build_user_context,
)

HOME = {"cluster_id": 0, "place_name": "บ้าน", "latitude": 13.7563,
        "longitude": 100.5018, "visit_frequency": 100, "avg_stay_time": 28800.0}
TEMPLE = {"cluster_id": 1, "place_name": "วัด", "latitude": 13.7601,
          "longitude": 100.5062, "visit_frequency": 40, "avg_stay_time": 3600.0,
          "radius_m": 400}


def at(utc_hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 20, utc_hour, minute, tzinfo=timezone.utc)


def fixes_at(place: dict, utc_hour: int, n: int) -> list:
    return [(place["latitude"], place["longitude"], at(utc_hour, i)) for i in range(n)]


# ── local hours ───────────────────────────────────────────────────────────────

def test_the_hour_is_the_patients_local_hour_not_utc():
    assert local_hour(at(0)) == LOCAL_UTC_OFFSET_HOURS
    # 20:00 UTC is 03:00 the next morning in Bangkok — the wrap has to hold, or
    # every late-evening routine lands in the wrong bucket.
    assert local_hour(at(20)) == 3


def test_a_naive_timestamp_is_read_as_utc():
    """SQLite hands timestamps back without a tzinfo."""
    assert local_hour(datetime(2026, 8, 20, 3, 0)) == local_hour(at(3))


# ── building ──────────────────────────────────────────────────────────────────

def test_probability_is_out_of_every_fix_in_the_hour_not_just_the_matched_ones():
    """A patient out walking most of an hour must not read as confidently home.

    Four fixes at home, six somewhere else entirely: 0.4, not 1.0.
    """
    away = (13.9000, 100.7000)
    fixes = fixes_at(HOME, 2, 4) + [(away[0], away[1], at(2, 10 + i)) for i in range(6)]

    patterns = build_routine_patterns(fixes, [HOME, TEMPLE])

    assert probability_at(patterns, local_hour(at(2)), 0) == pytest.approx(0.4)


def test_an_hour_with_too_little_evidence_says_nothing():
    """One stray fix is not a habit."""
    assert build_routine_patterns(fixes_at(HOME, 2, 3), [HOME]) == []
    assert build_routine_patterns(fixes_at(HOME, 2, 5), [HOME]) != []


def test_each_place_is_matched_against_its_own_radius():
    """The temple carries radius_m 400; a fix 250 m out belongs to it, not nowhere."""
    # ~250 m north of the temple.
    near_temple = (TEMPLE["latitude"] + 0.00225, TEMPLE["longitude"])
    fixes = [(near_temple[0], near_temple[1], at(4, i)) for i in range(8)]

    patterns = build_routine_patterns(fixes, [HOME, TEMPLE])

    assert probability_at(patterns, local_hour(at(4)), 1) == pytest.approx(1.0)


def test_nothing_is_learned_without_pins_or_without_history():
    assert build_routine_patterns(fixes_at(HOME, 2, 50), []) == []
    assert build_routine_patterns([], [HOME]) == []


def test_the_output_says_how_much_it_is_based_on():
    patterns = build_routine_patterns(fixes_at(HOME, 2, 12), [HOME])
    assert patterns[0]["samples"] == 12
    assert set(patterns[0]) == {"hour", "cluster_id", "probability", "samples"}


def test_decode_treats_a_broken_column_as_no_routine():
    assert decode(None) == []
    assert decode("") == []
    assert decode("{not json") == []
    assert decode('{"hour": 7}') == []          # an object, not a list
    assert decode('[{"hour": 7}, 5]') == [{"hour": 7}]


# ── the seam ──────────────────────────────────────────────────────────────────

def _ctx(now: datetime, patterns: list[dict]) -> UserContext:
    return UserContext(patient_id=1, current_lat=None, current_lng=None,
                       now=now, known_places=[HOME, TEMPLE],
                       routine_patterns=patterns)


def test_the_builder_and_the_scorer_agree_on_what_hour_it_is():
    """The failure this guards is silent.

    If the two ever read the hour through different offsets, every lookup misses,
    time_match reads 0.0 for every place at every hour, and the result is
    indistinguishable from a patient who simply has no routine — no error, no
    log line, just a factor that quietly never votes again.
    """
    moment = at(2)
    patterns = build_routine_patterns(fixes_at(HOME, 2, 20), [HOME, TEMPLE])

    scored = score_place(HOME, _ctx(moment, patterns))

    assert scored.factors["time_match"] > 0.0


def test_time_match_only_votes_when_there_is_a_routine_on_file():
    """A patient with no history scores exactly as they did before L3-4.

    Scored on the temple, not the home: the home is the top of both the
    frequency and the familiarity scale, so it sits at confidence 1.0 with or
    without a routine and would hide the difference.
    """
    moment = at(4)
    patterns = build_routine_patterns(fixes_at(TEMPLE, 4, 20), [HOME, TEMPLE])

    without = score_place(TEMPLE, _ctx(moment, []))
    with_routine = score_place(TEMPLE, _ctx(moment, patterns))

    assert without.factors["time_match"] == 0.0
    assert with_routine.factors["time_match"] == pytest.approx(1.0)
    # A place the patient is reliably at right now should rank higher than the
    # same place scored blind to the hour.
    assert with_routine.confidence > without.confidence


def test_being_somewhere_at_the_wrong_hour_scores_it_lower_than_the_right_hour():
    patterns = build_routine_patterns(fixes_at(TEMPLE, 4, 20), [HOME, TEMPLE])

    right = score_place(TEMPLE, _ctx(at(4), patterns)).confidence
    wrong = score_place(TEMPLE, _ctx(at(15), patterns)).confidence

    assert right > wrong


def test_a_profile_row_carries_the_routine_into_the_context():
    class Row:
        patient_id = 1
        known_places = json.dumps([HOME])
        routine_patterns = json.dumps([{"hour": 9, "cluster_id": 0, "probability": 0.8}])

    ctx = build_user_context(Row(), None, None, at(2))

    assert ctx.has_routine
    assert probability_at(ctx.routine_patterns, 9, 0) == 0.8


def test_a_profile_written_before_this_column_had_a_writer_still_loads():
    class Row:
        patient_id = 1
        known_places = json.dumps([HOME])
        routine_patterns = None

    ctx = build_user_context(Row(), None, None, at(2))

    assert ctx.has_routine is False
    assert ctx.has_profile is True


def test_a_caller_with_no_clock_scores_without_blowing_up():
    """``trip_confidence`` builds a context with ``now=None``.

    ``score_place`` did not read ``now`` before this factor existed, so nothing
    forced the question. A routine on file plus no clock means the hour cannot
    be looked up — which is "no opinion", not an exception.
    """
    ctx = UserContext(
        patient_id=1, current_lat=None, current_lng=None, now=None,
        known_places=[HOME, TEMPLE],
        routine_patterns=[{"hour": 9, "cluster_id": 0, "probability": 0.8}],
    )

    assert ctx.has_routine is False
    assert score_place(HOME, ctx).factors["time_match"] == 0.0


# ── the whole path, through the database ──────────────────────────────────────

@pytest.mark.asyncio
async def test_gps_history_and_pins_become_a_routine_the_recommender_can_read(
    db_session,
):
    """save GPS → pin places → build → store → read back out as time_match.

    scripts/build_routine_patterns.py is the only writer this column has, so
    without this the column's contents are never checked against its readers.
    """
    from app.db import crud
    from scripts.build_routine_patterns import build_for

    patient = await crud.create_user(
        db_session, firebase_uid="routine-uid", name="P", role="patient")
    await crud.upsert_behavioral_profile(
        db_session, patient.id, known_places=json.dumps([HOME, TEMPLE]))

    # Twelve fixes at the temple in one hour, three days ago so the 30-day
    # window holds them.
    base = datetime.now(timezone.utc) - timedelta(days=3)
    base = base.replace(minute=0, second=0, microsecond=0)
    for i in range(12):
        await crud.save_gps_point(
            db_session, patient.id, TEMPLE["latitude"], TEMPLE["longitude"],
            recorded_at=base + timedelta(minutes=i))
    await db_session.commit()

    summary = await build_for(db_session, patient.id, days=30, dry_run=False)
    assert "12 fixes" in summary

    profile = await crud.get_behavioral_profile(db_session, patient.id)
    ctx = build_user_context(profile, None, None, base)

    assert ctx.has_routine
    assert score_place(TEMPLE, ctx).factors["time_match"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_a_patient_with_no_pins_gets_no_invented_routine(db_session):
    """The guard that keeps this from becoming Module 1's clustering.

    Places come from a human or there are no places, so a patient with a month
    of GPS and nothing pinned learns nothing at all.
    """
    from app.db import crud
    from scripts.build_routine_patterns import build_for

    patient = await crud.create_user(
        db_session, firebase_uid="no-pins-uid", name="P", role="patient")
    base = datetime.now(timezone.utc) - timedelta(days=2)
    for i in range(40):
        await crud.save_gps_point(
            db_session, patient.id, 13.80, 100.60,
            recorded_at=base + timedelta(minutes=i))
    await db_session.commit()

    summary = await build_for(db_session, patient.id, days=30, dry_run=False)

    assert "no pins on file" in summary
    profile = await crud.get_behavioral_profile(db_session, patient.id)
    assert profile is None or profile.routine_patterns is None
