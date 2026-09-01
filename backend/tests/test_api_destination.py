"""``GET /api/predict-destination/{patient_id}`` — the Markov replacement.

The report gives Module 2 a destination predictor; the one that was built is an
LSTM and the 4-week plan cut it, so this path answered 404 and the app side was
told it never would. This endpoint reads the transition matrix ``RoutePredictor``
already fits on every risk score and returns the top rows of it.

**Most of these tests are about the honesty fields, not the ranking.** With four
pins and a handful of trips the matrix is close to uniform, and the failure this
project has already shipped once — the C-3 confidence ceiling, a number that
measured distance and was displayed as safety — is a plausible-looking percentage
with nothing behind it. ``scorer``, ``history_status`` and
``transitions_observed`` are what let a caller tell a counted 0.60 from a
divided-by-four 0.25, so they are pinned as hard as the ordering is.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import crud

pytestmark = pytest.mark.asyncio

HOME = (13.7563, 100.5018)
TEMPLE = (13.7601, 100.5062)
MARKET = (13.7530, 100.4990)
NOWHERE = (13.9000, 100.9000)     # inside no pin's radius

PLACES = [
    {"cluster_id": 0, "place_name": "บ้าน", "latitude": HOME[0], "longitude": HOME[1],
     "visit_frequency": 100, "avg_stay_time": 28800.0, "radius_m": 150,
     "source": "manual", "is_home": True},
    {"cluster_id": 1, "place_name": "วัด", "latitude": TEMPLE[0], "longitude": TEMPLE[1],
     "visit_frequency": 40, "avg_stay_time": 3600.0, "radius_m": 150,
     "source": "manual"},
    {"cluster_id": 2, "place_name": "ตลาด", "latitude": MARKET[0], "longitude": MARKET[1],
     "visit_frequency": 40, "avg_stay_time": 3600.0, "radius_m": 150,
     "source": "manual"},
]

# Inside the 30-day window ``get_gps_history`` reads — a fixed past date made
# every "ok" case return no_location instead, silently, because fit() had
# nothing to count and the endpoint correctly said so.
_START = datetime.now(timezone.utc) - timedelta(days=2)


async def make_patient(db_session, places=PLACES, uid="pt-dest"):
    caregiver = await crud.create_user(
        db_session, firebase_uid=f"cg-{uid}", name="cg", role="caregiver")
    await db_session.flush()
    patient = await crud.create_user(
        db_session, firebase_uid=uid, name="pt", role="patient",
        caregiver_id=caregiver.id, severity_level=1)
    await db_session.flush()
    if places is not None:
        await crud.upsert_behavioral_profile(
            db_session, patient.id,
            known_places=json.dumps(places, ensure_ascii=False))
    await db_session.commit()
    return patient.id


async def walk(db_session, patient_id, trips):
    """Record a journey as GPS points so ``fit()`` can count the transitions.

    ``trips`` is a list of (lat, lng) stops in order. One point per stop is
    enough — ``fit`` counts a transition whenever consecutive readings resolve
    to different clusters.
    """
    when = _START
    for lat, lng in trips:
        await crud.save_gps_point(db_session, patient_id, lat, lng, recorded_at=when)
        when += timedelta(minutes=30)
    await db_session.commit()


# ── the states that return nothing, and say why ──────────────────────────────

async def test_no_pins_says_so_instead_of_guessing(client, db_session):
    patient_id = await make_patient(db_session, places=None, uid="pt-nopins")

    r = await client.get(f"/api/predict-destination/{patient_id}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "no_profile"
    assert body["predictions"] == []
    # The message has to name the fix, because "no_profile" alone reads as broken.
    assert "places" in body["message"]


async def test_no_gps_and_no_coordinates_is_not_an_error(client, db_session):
    patient_id = await make_patient(db_session, uid="pt-nogps")

    r = await client.get(f"/api/predict-destination/{patient_id}")

    assert r.status_code == 200
    assert r.json()["status"] == "no_location"
    assert r.json()["predictions"] == []


async def test_standing_between_places_refuses_to_guess(client, db_session):
    """A Markov chain needs a starting state. Being off-map is the wandering
    case, which is Module 3's job — inventing a start here would answer the
    riskiest situation with the least evidence."""
    patient_id = await make_patient(db_session, uid="pt-away")
    await walk(db_session, patient_id, [HOME, TEMPLE, HOME])

    r = await client.get(
        f"/api/predict-destination/{patient_id}?lat={NOWHERE[0]}&lng={NOWHERE[1]}")

    assert r.status_code == 200
    assert r.json()["status"] == "unknown_current_place"
    assert r.json()["predictions"] == []
    # Point them at the endpoint that does handle it.
    assert "/api/risk" in r.json()["message"]


# ── the honesty fields ───────────────────────────────────────────────────────

async def test_it_never_claims_to_be_the_lstm(client, db_session):
    """`scorer` is the field that keeps the report's wording true. If the LSTM is
    ever restored it gets its own value, so a stored response stays traceable."""
    patient_id = await make_patient(db_session, uid="pt-scorer")
    await walk(db_session, patient_id, [HOME, TEMPLE, HOME])

    r = await client.get(
        f"/api/predict-destination/{patient_id}?lat={HOME[0]}&lng={HOME[1]}")

    assert r.json()["scorer"] == "markov"


async def test_a_place_never_left_reports_history_none(client, db_session):
    """The load-bearing one. `fit()` fills a row with no observed departures with
    1/n, so the endpoint would answer "33%" for three pins with zero evidence.
    That is division, not prediction, and it has to be labelled as such."""
    patient_id = await make_patient(db_session, uid="pt-never")
    # The patient has only ever been recorded at the market; nothing has been
    # seen leaving home.
    await walk(db_session, patient_id, [MARKET, MARKET])

    r = await client.get(
        f"/api/predict-destination/{patient_id}?lat={HOME[0]}&lng={HOME[1]}")

    body = r.json()
    assert body["status"] == "ok"
    assert body["history_status"] == "none"
    assert "ไม่ใช่คำทำนาย" in body["message"]


async def test_a_handful_of_trips_reports_history_sparse(client, db_session):
    patient_id = await make_patient(db_session, uid="pt-sparse")
    await walk(db_session, patient_id, [HOME, TEMPLE, HOME, TEMPLE, HOME])

    r = await client.get(
        f"/api/predict-destination/{patient_id}?lat={HOME[0]}&lng={HOME[1]}")

    body = r.json()
    assert body["history_status"] == "sparse"
    # The raw count is returned so the caller can see *why* it is sparse rather
    # than trusting the label.
    assert 0 < body["transitions_observed"] < 20


async def test_enough_trips_reports_history_ok(client, db_session):
    patient_id = await make_patient(db_session, uid="pt-ok")
    await walk(db_session, patient_id, [HOME, TEMPLE] * 12)

    r = await client.get(
        f"/api/predict-destination/{patient_id}?lat={HOME[0]}&lng={HOME[1]}")

    body = r.json()
    assert body["transitions_observed"] >= 20
    assert body["history_status"] == "ok"


# ── the prediction itself ────────────────────────────────────────────────────

async def test_it_ranks_the_place_they_actually_go_to_first(client, db_session):
    """Home → temple eight times, home → market twice. The temple must win, and
    by roughly the ratio observed — this is the whole method in one assertion."""
    patient_id = await make_patient(db_session, uid="pt-rank")
    trips = []
    for _ in range(8):
        trips += [HOME, TEMPLE]
    for _ in range(2):
        trips += [HOME, MARKET]
    await walk(db_session, patient_id, trips)

    r = await client.get(
        f"/api/predict-destination/{patient_id}?lat={HOME[0]}&lng={HOME[1]}")

    body = r.json()
    assert body["status"] == "ok"
    assert body["current_place_name"] == "บ้าน"
    top = body["predictions"][0]
    assert top["place_name"] == "วัด"
    assert top["rank"] == 1
    assert top["probability"] > 0.5
    # Ranked, not arbitrary order.
    probs = [p["probability"] for p in body["predictions"]]
    assert probs == sorted(probs, reverse=True)


async def test_it_never_predicts_the_place_they_are_standing_in(client, db_session):
    """`fit()` leaves the diagonal at 1/n on an unobserved row, which would
    answer "they are at home, they will go home"."""
    patient_id = await make_patient(db_session, uid="pt-diag")
    await walk(db_session, patient_id, [MARKET, MARKET])   # forces the uniform row

    r = await client.get(
        f"/api/predict-destination/{patient_id}?lat={HOME[0]}&lng={HOME[1]}")

    ids = [p["cluster_id"] for p in r.json()["predictions"]]
    assert r.json()["current_cluster_id"] == 0
    assert 0 not in ids


async def test_every_prediction_carries_a_name_and_coordinates(client, db_session):
    """The app draws these on a map and reads them aloud. A prediction with no
    coordinates is not renderable, and one with no name is not sayable."""
    patient_id = await make_patient(db_session, uid="pt-shape")
    await walk(db_session, patient_id, [HOME, TEMPLE, HOME, MARKET])

    r = await client.get(
        f"/api/predict-destination/{patient_id}?lat={HOME[0]}&lng={HOME[1]}")

    for p in r.json()["predictions"]:
        assert p["place_name"] in {"บ้าน", "วัด", "ตลาด"}
        assert -90 <= p["latitude"] <= 90
        assert -180 <= p["longitude"] <= 180
        assert p["probability_pct"] == round(p["probability"] * 100)


async def test_it_writes_nothing(client, db_session):
    """Unlike /api/risk and /api/search-area, both of which are GETs with side
    effects and must never be polled. This one is safe to call twice."""
    patient_id = await make_patient(db_session, uid="pt-readonly")
    await walk(db_session, patient_id, [HOME, TEMPLE, HOME])

    before = len(await crud.get_alerts(db_session, patient_id))
    await client.get(f"/api/predict-destination/{patient_id}?lat={HOME[0]}&lng={HOME[1]}")
    await client.get(f"/api/predict-destination/{patient_id}?lat={HOME[0]}&lng={HOME[1]}")
    after = len(await crud.get_alerts(db_session, patient_id))

    assert before == after == 0
    assert await crud.get_latest_risk_score(db_session, patient_id) is None
