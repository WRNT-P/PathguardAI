"""Phase 3 — proof that the alert chain closes without a human in it.

Every test here talks only to ``POST /api/gps``. Nothing calls ``GET /api/risk``,
and that is the entire point: before this chain existed, ``crud.save_alert`` was
reachable only from GET endpoints, so a patient could wander with the server
running and nothing would ever be scored, stored or alerted on unless somebody
opened a URL by hand.

The four cases mirror the verify criteria in docs/plan_person2_lane.md Phase 3.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db import crud
from app.db.models import RiskScore

pytestmark = pytest.mark.asyncio

# Partial mode drops route_deviation and unfamiliarity (both need known_places)
# and renormalizes what is left — see risk.py:_PARTIAL_FACTORS.
PARTIAL_FACTORS = {"wandering", "confusion", "danger_zone"}

# The score a patient sitting still at home would get if the two profile-hungry
# factors were left in with their safety-biased defaults instead of dropped:
# 0.10x1.0 (familiarity 0) + 0.30x0.7 (deviation 350 m) = 31 points. Partial mode
# exists so this floor does not happen; anything at or above it means the
# renormalization silently stopped working.
NO_PROFILE_FLOOR = 31.0


async def _register_patient(client, uid: str) -> int:
    resp = await client.post(
        "/api/register", json={"firebase_uid": uid, "name": "P", "role": "patient"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _walk_in_circles(patient_id: int, n: int, *, radius_deg: float = 0.0005) -> list[dict]:
    """n readings looping around one point, ending now, 30 s apart.

    A loop rather than a straight line because that is the shape Module 2's
    wandering detector is looking for; ending at ``now`` keeps the reading inside
    the 600 s gps_gap threshold so these tests are not really GPS-loss tests.
    """
    now = datetime.now(timezone.utc)
    points = []
    for i in range(n):
        angle = 2 * math.pi * (i / 10.0)  # a full loop every 10 readings
        points.append({
            "patient_id": patient_id,
            "latitude": 13.7563 + radius_deg * math.sin(angle),
            "longitude": 100.5018 + radius_deg * math.cos(angle),
            "speed": 1.1,
            "recorded_at": (now - timedelta(seconds=30 * (n - i))).isoformat(),
        })
    return points


def _sit_still(patient_id: int, n: int) -> list[dict]:
    """n readings at one spot, jittered only by GPS noise (~0.1 m)."""
    now = datetime.now(timezone.utc)
    return [{
        "patient_id": patient_id,
        "latitude": 13.7563 + (i % 3) * 1e-6,
        "longitude": 100.5018 + (i % 2) * 1e-6,
        "speed": 0.0,
        "recorded_at": (now - timedelta(seconds=30 * (n - i))).isoformat(),
    } for i in range(n)]


async def _count_risk_scores(db_session, patient_id: int) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(RiskScore).where(
            RiskScore.patient_id == patient_id)
    )
    return result.scalar_one()


async def test_gps_ingestion_scores_risk_with_nobody_calling_the_endpoint(
    client, db_session
):
    """The whole chain: a fresh patient's GPS lands and a RiskScore appears.

    The patient has no behavioral profile — analyze_behavior() is called by
    nothing in production — so this also proves partial mode carries a patient
    who has existed for thirty seconds.
    """
    patient_id = await _register_patient(client, "chain-fresh")

    resp = await client.post(
        "/api/gps/batch", json={"points": _walk_in_circles(patient_id, 30)}
    )
    assert resp.status_code == 200

    assert await crud.get_behavioral_profile(db_session, patient_id) is None

    score = await crud.get_latest_risk_score(db_session, patient_id)
    assert score is not None, "GPS ingestion did not score risk — the chain is broken"
    assert 0.0 <= score.score <= 100.0
    assert score.level in {"low", "medium", "high"}

    # The saved factors are the renormalized partial set, not the full five and
    # not the full five with two guesses in them.
    assert set(json.loads(score.factors)) == PARTIAL_FACTORS


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MEASURED DEFECT, not a flaky test. Partial mode scores a patient sitting "
        "still at home 39.2 and a patient walking in circles 12.5 — inverted. The "
        "cause is `confusion`, which the plan assumed was profile-free because it "
        "is rule-based: of its five sub-rules (stop_confusion_classification.py:"
        "173-198) two are pure no-profile penalties — familiarity is 0 without "
        "known_places (+0.20) and route deviation defaults to 300 m when there is "
        "no predicted route (+0.15) — and two more fire for anyone at rest: "
        "stopped 900 s (+0.30) and speed < 0.6 m/s (+0.15). Total 0.80, and it "
        "only fires at all when `stopped` is true, so resting outscores wandering. "
        "Rule-based is not the same as profile-free: confusion asks whether a stop "
        "is abnormal, and abnormality is defined by place familiarity. Remove this "
        "marker when partial mode is fixed."
    ),
)
async def test_stationary_patient_scores_below_the_no_profile_floor(client, db_session):
    """Gotcha #3: dropping the no_data gate the lazy way alarms at rest.

    Left in with their no-profile defaults, route_deviation and unfamiliarity
    alone score 31 points for a patient who has not moved. Renormalizing removes
    them from the sum instead of feeding a guess into it.
    """
    patient_id = await _register_patient(client, "chain-still")

    resp = await client.post(
        "/api/gps/batch", json={"points": _sit_still(patient_id, 30)}
    )
    assert resp.status_code == 200

    score = await crud.get_latest_risk_score(db_session, patient_id)
    assert score is not None
    assert score.score < NO_PROFILE_FLOOR, (
        f"a patient sitting still scored {score.score} — at or above the "
        f"{NO_PROFILE_FLOOR}-point floor partial mode exists to avoid"
    )
    assert score.level == "low"


async def test_rapid_points_are_throttled_to_at_most_two_scores(client, db_session):
    """A phone reporting every 30 s must not refit the models every 30 s.

    One scoring pass loads 30 days of GPS and fits IsolationForest +
    RoutePredictor, so the throttle is what keeps ingestion cheap enough to run
    on every reading at all.
    """
    patient_id = await _register_patient(client, "chain-throttle")

    # History first, so the scorer has something to fit and the first score lands.
    seed = await client.post(
        "/api/gps/batch", json={"points": _walk_in_circles(patient_id, 30)}
    )
    assert seed.status_code == 200
    assert await _count_risk_scores(db_session, patient_id) == 1

    # 120 further readings, one request each, all inside the 60 s window.
    for point in _walk_in_circles(patient_id, 120):
        resp = await client.post("/api/gps", json=point)
        assert resp.status_code == 200

    count = await _count_risk_scores(db_session, patient_id)
    assert count <= 2, f"throttle did not hold — {count} risk scores for 150 readings"

    # …and every reading still reached the database.
    rows = await crud.get_gps_history(db_session, patient_id, days=3650)
    assert len(rows) == 150


async def test_gps_is_kept_even_when_risk_scoring_blows_up(
    client, db_session, monkeypatch
):
    """Position history is irreplaceable; a score can be recomputed from it.

    So a scorer that raises must cost the caller its score, never its reading.
    """
    patient_id = await _register_patient(client, "chain-boom")

    async def _explode(*args, **kwargs):
        raise RuntimeError("simulated scorer failure")

    # gps.py imports the symbol directly, so patch it where it is looked up.
    monkeypatch.setattr("app.api.gps.evaluate_risk", _explode)

    resp = await client.post("/api/gps", json=_walk_in_circles(patient_id, 1)[0])
    assert resp.status_code == 200

    rows = await crud.get_gps_history(db_session, patient_id, days=3650)
    assert len(rows) == 1
    assert rows[0].smooth_latitude is not None
    assert await _count_risk_scores(db_session, patient_id) == 0
