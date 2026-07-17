"""Phase 4 — end-to-end integration test for issue #1.

Drives the REAL pipeline on the in-memory SQLite DB, no TensorFlow, no GeoLife
files needed. Mirrors the production flow proven manually on Neon (patient 13):

  1. seed a normal routine (spread-out, moving) as the training window,
  2. run Module 1 `analyze_behavior` -> a clean behavioral profile (>=5 places),
  3. inject a pacing+dwell wandering episode (reusing scripts.inject_wandering's
     geometry) at an UNFAMILIAR, off-route spot + a co-located danger zone, all
     flagged synthetic_injected=True — the "current session",
  4. call Modules 3/4/5 through their real handlers and Module 2's TF-free
     wandering detector, asserting the injected anomaly drives a HIGH risk score
     (>80) + emergency, traceable to the synthetic rows.

Module 2's LSTM destination-prediction path needs TensorFlow (excluded here for
the same reason conftest's client fixture omits its router); its sklearn
wandering detector — the part Module 3 consumes — IS exercised.
"""
from datetime import datetime, timedelta, timezone

import json
import pytest
from sqlalchemy import select

from app.ai.module1_behavior.behavior_pipeline import analyze_behavior
from app.ai.module2_prediction.wandering_detection import WanderingDetector
from app.api.recommendation import get_recommendations
from app.api.risk import get_risk
from app.api.search_area import get_search_area
from app.db import crud
from app.db.models import Alert, DangerZone, GPSData
from scripts.inject_wandering import _offset, build_pacing, to_gps_rows

pytestmark = pytest.mark.asyncio

# Six familiar places (Bangkok), each far from the others so DBSCAN forms >=5
# clusters and the routine reads as "moving between spread-out places".
_PLACES = [
    (13.7460, 100.5340), (13.7510, 100.5400), (13.7580, 100.5300),
    (13.7400, 100.5450), (13.7620, 100.5480), (13.7350, 100.5250),
]


async def _seed_normal_routine(db, patient_id, days=25):
    """~25 days of moving GPS visiting the familiar places (training window)."""
    now = datetime.now(timezone.utc)
    for d in range(days, 0, -1):  # oldest -> newest, all older than the injection
        day0 = now - timedelta(days=d)
        for visit, (plat, plon) in enumerate(_PLACES):
            for k in range(8):  # >=5 pts within ~10 m => a cluster
                jlat, jlon = _offset(plat, plon, k * 1.5, k * 1.5)
                await crud.save_gps_point(
                    db, patient_id, latitude=jlat, longitude=jlon,
                    speed=1.2, recorded_at=day0 + timedelta(hours=visit, minutes=k),
                )
    await db.commit()


async def test_phase4_full_pipeline_high_risk_from_injected_segment(db_session):
    db = db_session
    user = await crud.create_user(db, firebase_uid="geolife_test", name="Pat", role="patient")
    await db.flush()
    pid = user.id

    # ── 1-2. normal routine -> Module 1 builds a clean profile ────────────────
    await _seed_normal_routine(db, pid)
    res = await analyze_behavior(db, pid, days=30)
    await db.commit()
    places = res["places"]
    assert len(places) >= 5, f"Module 1 should learn >=5 known places, got {len(places)}"

    profile = await crud.get_behavioral_profile(db, pid)
    assert profile and profile.known_places, "profile must be persisted"

    # ── 3. inject pacing+dwell at an unfamiliar, off-route anchor + danger zone ─
    anchor_lat, anchor_lon = _offset(_PLACES[0][0], _PLACES[0][1], 3000.0, 3000.0)
    end_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    pacing = build_pacing(anchor_lat, anchor_lon, leg_m=40.0, laps=40,
                          speed=0.4, step_s=15, end_at=end_at, dwell_pts=5, dwell_step_s=60)
    injected = to_gps_rows(pacing, pid)
    db.add_all(injected)
    db.add(DangerZone(
        name="Demo hazard @ test wandering site",
        center_latitude=anchor_lat, center_longitude=anchor_lon,
        radius_meters=300.0, zone_type="waterway", active=True,
        synthetic_injected=True,
        source_reference="issue #1 Phase 2.5 (test)",
        rationale="co-located hazard at the wandering/confusion site",
        created_by="test",
    ))
    await db.commit()

    # ── 4a. Module 3 — HIGH risk + emergency, driven by the injected segment ──
    risk = (await get_risk(patient_id=pid, lat=None, lng=None, db=db)).model_dump()
    await db.commit()
    assert risk["status"] == "ok"
    assert risk["risk_score"] > 80, f"expected HIGH risk from injection, got {risk}"
    assert risk["risk_level"] == "high"
    assert risk["emergency"] is True
    # an alert row was written
    alerts = (await db.execute(select(Alert).where(Alert.patient_id == pid))).scalars().all()
    assert alerts, "an emergency should persist an Alert row"

    # ── 4b. traceability — the scored 'current' position IS an injected point ──
    latest = await crud.get_latest_gps(db, pid)
    assert latest.synthetic_injected is True, "current position must trace to the injected segment"
    n_injected = await db.scalar(
        select(GPSData).where(GPSData.patient_id == pid, GPSData.synthetic_injected.is_(True)).limit(1))
    assert n_injected is not None

    # ── 4c. Module 2 (TF-free wandering detector) flags the injected window ────
    real = (await db.execute(select(GPSData).where(
        GPSData.patient_id == pid, GPSData.synthetic_injected.is_(False)).order_by(GPSData.recorded_at))).scalars().all()
    inj = (await db.execute(select(GPSData).where(
        GPSData.patient_id == pid, GPSData.synthetic_injected.is_(True)).order_by(GPSData.recorded_at))).scalars().all()
    det = WanderingDetector(); det.fit(real)
    w = det.detect(inj)
    assert w["status"] == "ok"
    assert w["wandering_level"] in {"mild", "high"}, w

    # ── 4d. Module 4 — search area. With GPS live it correctly returns
    #        'gps_active' (no search needed). Forcing a loss (stale last position)
    #        exercises the actual radius computation. ───────────────────────────
    live = (await get_search_area(
        patient_id=pid, last_lat=None, last_lng=None, last_speed_ms=None,
        last_direction_deg=None, time_missing_minutes=30, db=db)).model_dump()
    assert live["status"] == "gps_active", live  # module ran, GPS still live

    # simulate a lost patient: last known point is old -> search radius computed
    lost = (await get_search_area(
        patient_id=pid, last_lat=anchor_lat, last_lng=anchor_lon, last_speed_ms=1.0,
        last_direction_deg=None, time_missing_minutes=120, db=db)).model_dump()
    assert lost["status"] == "ok" and "Radius" in lost["message"], lost

    # ── 4e. Module 5 — recommendations returned ───────────────────────────────
    rec = (await get_recommendations(patient_id=pid, lat=None, lng=None, db=db)).model_dump()
    await db.commit()
    assert rec.get("status") in {"ok", None} or rec.get("recommendations") is not None, rec
