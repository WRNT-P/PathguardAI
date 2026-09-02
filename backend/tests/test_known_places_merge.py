"""The two writers of ``known_places`` must not destroy each other's work.

``behavioral_profiles.known_places`` has two authors: the caregiver, through
``POST /api/patients/{id}/places``, and Module 1's clustering, through
``analyze_behavior``. Both failures this could cause are silent, which is why
they are tested here rather than trusted to review.

The first is obvious once stated — Module 1 used to overwrite the column
wholesale, so the first night the job ran, every pin the caregiver had entered
would be gone and nobody would be told.

The second is not obvious at all. Every consumer of this column normalizes
*relatively*: ``cluster_matcher.get_familiarity`` divides by the largest
``visit_frequency`` in the list, Module 5 divides by the largest
``avg_stay_time``. A pin carries the rank scale (100 for "lives here") and
seconds; clustering emits a raw count of GPS fixes — measured at 2,978 in a
30-day window — and minutes. Merge those two without rescaling and the pins are
not deleted, they are drowned: the caregiver's home pin reads as 3% familiar
while the patient sits in their own living room, and the column still looks
perfectly fine in the admin screen.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.module1_behavior.behavior_pipeline import analyze_behavior
from app.ai.module1_behavior.known_places import (
    LEARNED_VISIT_CEILING, decode, merge_learned, normalize_learned, renumber,
)
from app.ai.module2_prediction.cluster_matcher import get_familiarity
from app.db import crud

# What the pin form produces for "she lives here" / "most days" / "weekly".
HOME_PIN = {
    "cluster_id": 0, "place_name": "บ้าน", "latitude": 13.7563, "longitude": 100.5018,
    "visit_frequency": 100, "avg_stay_time": 28800.0, "radius_m": 150,
    "visit_rank": "daily_live", "stay_rank": "all_day", "source": "manual",
}
MARKET_PIN = {
    "cluster_id": 1, "place_name": "ตลาด", "latitude": 13.7600, "longitude": 100.5050,
    "visit_frequency": 40, "avg_stay_time": 7200.0, "radius_m": 300,
    "visit_rank": "most_days", "stay_rank": "few_hours", "source": "manual",
}

# What cluster_places actually returns: a raw fix count and minutes. The 2,978 is
# the real measured size of the largest cluster in a 30-day GeoLife window.
LEARNED_BLOB = {
    "cluster_id": 0, "latitude": 13.9900, "longitude": 100.9900,
    "visit_frequency": 2978, "avg_stay_time": 353.1,
}
LEARNED_SMALL = {
    "cluster_id": 1, "latitude": 13.8000, "longitude": 100.6000,
    "visit_frequency": 9, "avg_stay_time": 12.0,
}


# ── The merge rules, on their own ────────────────────────────────────────────

def test_a_pin_survives_a_learned_place_that_dwarfs_it():
    """The headline: merged, a home pin must still read as the familiar place."""
    merged = merge_learned([HOME_PIN, MARKET_PIN], [LEARNED_BLOB, LEARNED_SMALL])

    home = next(p for p in merged if p.get("place_name") == "บ้าน")
    assert get_familiarity(merged, home["cluster_id"]) == 1.0


def test_unscaled_learned_frequencies_would_have_drowned_it():
    """The failure this prevents, spelled out — merge without the rescale."""
    naive = renumber([HOME_PIN, MARKET_PIN, LEARNED_BLOB, LEARNED_SMALL])

    assert get_familiarity(naive, 0) == pytest.approx(100 / 2978, abs=1e-4)
    assert get_familiarity(naive, 0) < 0.05, "home reads as a strange place"


def test_no_learned_place_can_outrank_a_daily_live_pin():
    """Policy, not tuning: only a human gets to say where someone lives."""
    scaled = normalize_learned([LEARNED_BLOB, LEARNED_SMALL])

    assert max(p["visit_frequency"] for p in scaled) == LEARNED_VISIT_CEILING
    assert LEARNED_VISIT_CEILING < HOME_PIN["visit_frequency"]


def test_learned_places_keep_their_order_relative_to_each_other():
    scaled = normalize_learned([LEARNED_BLOB, LEARNED_SMALL])

    assert scaled[0]["visit_frequency"] > scaled[1]["visit_frequency"]
    # A clustered place is somewhere the patient goes; it must never round to
    # zero, which would claim they have never been there.
    assert scaled[1]["visit_frequency"] >= 1


def test_stay_time_is_converted_from_minutes_to_the_seconds_pins_use():
    """Module 5 divides by max(avg_stay_time) — mixing units breaks it as surely
    as mixing frequencies does."""
    scaled = normalize_learned([LEARNED_BLOB])

    assert scaled[0]["avg_stay_time"] == pytest.approx(353.1 * 60)
    # 5.9 hours of dwell should now be comparable to an 8-hour pin, not to 6 min.
    assert scaled[0]["avg_stay_time"] < HOME_PIN["avg_stay_time"]


def test_cluster_ids_stay_contiguous_after_merging():
    """route_prediction.py:111 sizes an n x n matrix with max(cluster_id) + 1."""
    merged = merge_learned([HOME_PIN, MARKET_PIN], [LEARNED_BLOB, LEARNED_SMALL])

    assert [p["cluster_id"] for p in merged] == [0, 1, 2, 3]


def test_learned_rows_are_tagged_so_the_other_writer_can_recognise_them():
    merged = merge_learned([HOME_PIN], [LEARNED_BLOB])

    assert [p["source"] for p in merged] == ["manual", "learned"]


def test_decode_tolerates_a_column_it_cannot_read():
    assert decode(None) == []
    assert decode("") == []
    assert decode("not json") == []
    assert decode('{"places": 1}') == []          # not a list
    assert decode('[1, {"a": 2}]') == [{"a": 2}]  # keeps only the usable rows


# ── Through the real nightly job ─────────────────────────────────────────────

async def _seed_two_places(db, patient_id: int, days: int = 10) -> None:
    """Enough real *stays* (>=15 continuous minutes near one spot) for the
    stay-point pipeline to learn something to merge in — a dense burst of
    fixes a couple of minutes apart doesn't qualify as a stay any more."""
    now = datetime.now(timezone.utc)
    for d in range(days, 0, -1):
        day0 = now - timedelta(days=d)
        for visit, (plat, plon) in enumerate([(13.7460, 100.5340),
                                              (13.7510, 100.5400)]):
            for k in range(8):
                await crud.save_gps_point(
                    db, patient_id,
                    latitude=plat + k * 0.00002, longitude=plon + k * 0.00002,
                    speed=1.2, recorded_at=day0 + timedelta(hours=visit, minutes=k * 3),
                )
    await db.commit()


async def _pinned_patient(db) -> int:
    user = await crud.create_user(db, firebase_uid="pins", name="P", role="patient")
    await db.flush()
    await crud.upsert_behavioral_profile(
        db, user.id, known_places=json.dumps([HOME_PIN, MARKET_PIN],
                                             ensure_ascii=False),
    )
    await db.commit()
    return user.id


async def test_the_nightly_job_no_longer_deletes_the_caregivers_pins(db_session):
    patient_id = await _pinned_patient(db_session)
    await _seed_two_places(db_session, patient_id)

    result = await analyze_behavior(db_session, patient_id, days=30)
    await db_session.commit()

    names = [p.get("place_name") for p in result["places"]]
    assert "บ้าน" in names and "ตลาด" in names
    assert len(result["places"]) > 2, "and it still learned something new"


async def test_running_the_nightly_job_twice_does_not_pile_up_places(db_session):
    """Learned rows are replaced each run, not appended — otherwise a month of
    nightly runs buries the pins by sheer count."""
    patient_id = await _pinned_patient(db_session)
    await _seed_two_places(db_session, patient_id)

    first = await analyze_behavior(db_session, patient_id, days=30)
    await db_session.commit()
    second = await analyze_behavior(db_session, patient_id, days=30)
    await db_session.commit()

    assert len(second["places"]) == len(first["places"])
    assert sum(p.get("source") == "manual" for p in second["places"]) == 2


async def test_after_the_nightly_job_the_patient_is_still_familiar_with_home(
    db_session
):
    """End to end, through the same function Module 3 calls to score."""
    patient_id = await _pinned_patient(db_session)
    await _seed_two_places(db_session, patient_id)

    result = await analyze_behavior(db_session, patient_id, days=30)
    await db_session.commit()

    home = next(p for p in result["places"] if p.get("place_name") == "บ้าน")
    assert get_familiarity(result["places"], home["cluster_id"]) == 1.0
