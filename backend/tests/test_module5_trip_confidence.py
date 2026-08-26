"""Scoring a destination the patient has never been — the input C-3 always gets.

The measurement that forced this module is pinned here as a regression: ask
``score_place`` about an unvisited destination and its confidence cannot exceed
0.350 however sensible the trip is, because ``frequency`` and ``familiarity``
are both defined relative to places already in the profile.
``test_a_sensible_new_trip_beats_the_old_ceiling`` is the test that fails if
anyone reverts to that behaviour.

Everything here is a pure function over dicts — no DB, no Firebase, no fixtures
beyond a profile literal — which is the point of keeping the scorer in ``ai/``.
"""
from __future__ import annotations

import math

import pytest

from app.ai.module5_recommend.recommendation_generation import score_place
from app.ai.module5_recommend.trip_confidence import score_destination
from app.ai.module5_recommend.user_context_analysis import UserContext

# The rule KB's route_deviation_ceiling_m. The API passes this in; the tests
# state it explicitly so a KB change shows up here as a deliberate edit.
CEILING_M = 500.0

HOME = (13.7563, 100.5018)

# Derived from the same earth radius cluster_matcher.haversine_km uses, so a
# "500 m north" in this file is 500 m to the code under test. The textbook
# 111_320 figure is a different sphere and leaves ~0.7 m of slack at 500 m —
# enough to turn an exact-zero assertion into 0.0015.
METRES_PER_DEG_LAT = 2 * math.pi * 6_371_000.0 / 360.0


def north(origin: tuple[float, float], metres: float) -> tuple[float, float]:
    """A point ``metres`` due north — latitude only, so the maths stays obvious."""
    return (origin[0] + metres / METRES_PER_DEG_LAT, origin[1])


def place(cluster_id, name, coords, freq, radius_m=150, stay=28800.0):
    return {"cluster_id": cluster_id, "place_name": name,
            "latitude": coords[0], "longitude": coords[1],
            "visit_frequency": freq, "avg_stay_time": stay,
            "radius_m": radius_m, "source": "manual"}


TEMPLE = north(HOME, 600)
PROFILE = [place(0, "บ้าน", HOME, 100), place(1, "วัด", TEMPLE, 40)]

# The decay tests walk north past 600 m, which is inside the temple's own radius
# — measuring fall-off there would measure the temple, not the fall-off. They use
# a single-pin profile so the only thing in the answer is distance from home.
HOME_ONLY = [place(0, "บ้าน", HOME, 100)]


def score(coords, profile=PROFILE, zones=(), **kw):
    return score_destination(coords[0], coords[1], profile, list(zones),
                             decay_ceiling_m=CEILING_M, **kw)


# ── the reason this module exists ────────────────────────────────────────────

def test_a_sensible_new_trip_beats_the_old_ceiling():
    """A new place near home must be able to out-score Module 5's 0.350 cap.

    ``score_place`` on the same destination is included so the comparison is
    made against the real function, not a remembered number.
    """
    destination = north(HOME, 200)                 # 50 m outside home's radius
    ctx = UserContext(patient_id=1, now=None, known_places=PROFILE,
                      current_lat=HOME[0], current_lng=HOME[1])
    old = score_place(
        {"cluster_id": 99, "latitude": destination[0], "longitude": destination[1],
         "visit_frequency": 0, "avg_stay_time": 0.0}, ctx)

    new = score(destination)
    assert old.confidence <= 0.35            # the ceiling, still there
    assert new.confidence > old.confidence
    assert new.confidence > 0.35


# ── familiarity for somewhere never visited ──────────────────────────────────

def test_destination_inside_a_pin_takes_that_pin_familiarity():
    inside_home = north(HOME, 100)                 # home radius is 150 m
    r = score(inside_home)
    assert r.status == "ok"
    assert r.confidence == 1.0                     # home is the most-visited pin
    assert r.nearest_place_name == "บ้าน"
    assert r.nearest_place_distance_m == 0.0


def test_a_less_visited_pin_scores_lower_than_home():
    assert score(TEMPLE).confidence < score(HOME).confidence


def test_familiarity_falls_off_with_distance():
    near = score(north(HOME, 250), profile=HOME_ONLY).confidence   # 100 m outside
    far = score(north(HOME, 550), profile=HOME_ONLY).confidence    # 400 m outside
    assert near > far > 0


def test_familiarity_reaches_zero_at_the_ceiling():
    at_ceiling = north(HOME, 150 + CEILING_M)      # radius + ceiling
    assert score(at_ceiling, profile=HOME_ONLY).confidence == 0.0


def test_beyond_the_ceiling_stays_at_zero_and_never_goes_negative():
    assert score(north(HOME, 5_000), profile=HOME_ONLY).confidence == 0.0


def test_the_ceiling_comes_from_the_caller_not_the_module():
    """An admin widening route_deviation_ceiling_m must move this scale too."""
    destination = north(HOME, 550)
    tight = score_destination(*destination, HOME_ONLY, [], decay_ceiling_m=200.0)
    wide = score_destination(*destination, HOME_ONLY, [], decay_ceiling_m=2000.0)
    assert wide.confidence > tight.confidence


# ── the danger-zone veto ─────────────────────────────────────────────────────

def test_danger_zone_vetoes_even_the_patient_own_home():
    """A weighted blend would hand back ~0.5 here. The caregiver drew that circle."""
    zone = {"id": 1, "name": "คลอง", "latitude": HOME[0], "longitude": HOME[1],
            "radius_m": 200, "zone_type": "water"}
    r = score(HOME, zones=[zone])
    assert r.confidence == 0.0
    assert r.factors["danger_zone"] is True
    assert r.blocking_zone_name == "คลอง"


def test_a_zone_that_does_not_contain_the_destination_changes_nothing():
    far_zone = {"id": 1, "name": "ทางด่วน", "latitude": north(HOME, 5_000)[0],
                "longitude": HOME[1], "radius_m": 100, "zone_type": "road"}
    assert score(HOME, zones=[far_zone]).confidence == score(HOME).confidence


# ── no profile is not the same as "unsafe" ───────────────────────────────────

def test_empty_profile_says_so_instead_of_scoring_zero():
    r = score(north(HOME, 300), profile=[])
    assert r.status == "no_profile"
    assert r.factors["danger_zone"] is False


# ── per-place radius ─────────────────────────────────────────────────────────

def test_a_wide_pin_matches_where_a_house_sized_one_would_not():
    """A market compound is not judged by a 150 m house radius (gotcha 8)."""
    market = north(HOME, 3_000)
    wide = [place(0, "ตลาด", market, 100, radius_m=400)]
    narrow = [place(0, "ตลาด", market, 100, radius_m=150)]
    edge_of_market = north(market, 300)

    assert score(edge_of_market, profile=wide).confidence == 1.0
    assert score(edge_of_market, profile=narrow).confidence < 1.0


def test_nearest_is_measured_to_the_edge_not_the_centre():
    """A wide place whose boundary is closer must win over a nearer centre."""
    wide = place(0, "โรงพยาบาล", north(HOME, 1_000), 100, radius_m=800)
    narrow = place(1, "ร้านกาแฟ", north(HOME, 600), 10, radius_m=50)
    destination = north(HOME, 400)

    r = score(destination, profile=[wide, narrow])
    # Centre distances: hospital 600 m, cafe 200 m — the cafe is nearer.
    # Edge distances: hospital 0 m (inside), cafe 150 m — the hospital contains it.
    assert r.nearest_place_name == "โรงพยาบาล"


def test_pins_without_a_radius_still_work():
    """Profiles written before radius_m existed must behave as they did."""
    legacy = [{"cluster_id": 0, "place_name": "บ้าน", "latitude": HOME[0],
               "longitude": HOME[1], "visit_frequency": 100, "avg_stay_time": 1.0}]
    assert score(north(HOME, 100), profile=legacy).confidence == 1.0


# ── shape ────────────────────────────────────────────────────────────────────

def test_rules_are_never_reported_as_ml():
    assert score(HOME).scorer == "rules"


@pytest.mark.parametrize("metres", [0, 50, 200, 600, 1_500, 10_000])
def test_confidence_is_always_a_probability(metres):
    assert 0.0 <= score(north(HOME, metres)).confidence <= 1.0
    assert 0.0 <= score(north(HOME, metres), profile=HOME_ONLY).confidence <= 1.0
