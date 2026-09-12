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
from tests.conftest import record_arrival

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
            arrived_at = day0 + timedelta(hours=visit)
            for k in range(8):  # spans ~21 min -> a real stay, not a passing fix
                jlat, jlon = _offset(plat, plon, k * 1.5, k * 1.5)
                await crud.save_gps_point(
                    db, patient_id, latitude=jlat, longitude=jlon,
                    speed=1.2, recorded_at=arrived_at + timedelta(minutes=k * 3),
                )
            # The trip the patient chose in the app and completed. Module 1
            # learns places from these, not from the track alone, so a routine
            # seeded only as GPS teaches it nothing (see trip_learning.py).
            await record_arrival(db, patient_id, plat, plon,
                                 f"Place {visit}", arrived_at)
    await db.commit()


async def _confirm_learned_places(db, pid: int) -> None:
    """Mark everything Module 1 learned as confirmed by a caregiver.

    Clustered places are deliberately excluded from safety decisions until a
    human confirms them (see risk_data_collection._extract_known_places), and
    these tests are about what happens to a patient who HAS a settled profile —
    so the confirmation a real caregiver would give is done here.
    """
    profile = await crud.get_behavioral_profile(db, pid)
    places = json.loads(profile.known_places)
    for place in places:
        place["source"] = "manual"
    await crud.upsert_behavioral_profile(
        db, patient_id=pid, known_places=json.dumps(places, ensure_ascii=False))
    await db.flush()


async def test_phase4_full_pipeline_high_risk_from_injected_segment(db_session):
    db = db_session
    user = await crud.create_user(db, firebase_uid="geolife_test", name="Pat", role="patient")
    await db.flush()
    pid = user.id

    # ── 1-2. normal routine -> Module 1 builds a clean profile ────────────────
    await _seed_normal_routine(db, pid)
    res = await analyze_behavior(db, pid, days=30)
    await _confirm_learned_places(db, pid)
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


async def test_phase4_safe_zone_exit_alert_and_real_distance_scaling(db_session):
    """A patient with a real behavioral profile who wanders far outside every
    known place must (a) get a standalone safe_zone_exit alert, independent of
    the weighted score, and (b) have route_deviation reflect the ACTUAL
    distance rather than a flat constant — the two bugs the user hit: no alert
    fired at all when far from home, and risk capped at "medium" because
    route_deviation didn't scale with real distance. No danger zone here, to
    isolate the safe-zone condition from the existing geofence one.
    """
    db = db_session
    user = await crud.create_user(db, firebase_uid="safe_zone_test", name="Pat", role="patient")
    await db.flush()
    pid = user.id

    await _seed_normal_routine(db, pid)
    res = await analyze_behavior(db, pid, days=30)
    await _confirm_learned_places(db, pid)
    await db.commit()
    assert len(res["places"]) >= 5

    # ── control: near home -> no safe_zone_exit alert, low route_deviation ────
    near_lat, near_lon = _offset(_PLACES[0][0], _PLACES[0][1], 20.0, 20.0)
    near = (await get_risk(patient_id=pid, lat=near_lat, lng=near_lon, db=db)).model_dump()
    await db.commit()
    assert near["status"] == "ok"
    near_alerts = (await db.execute(
        select(Alert).where(Alert.patient_id == pid, Alert.alert_type == "safe_zone_exit")
    )).scalars().all()
    assert not near_alerts, "no safe_zone_exit alert expected near home"

    # ── far from every known place -> safe_zone_exit alert fires ──────────────
    far_lat, far_lon = _offset(_PLACES[0][0], _PLACES[0][1], 60_000.0, 60_000.0)  # ~60 km
    far = (await get_risk(patient_id=pid, lat=far_lat, lng=far_lon, db=db)).model_dump()
    await db.commit()
    assert far["status"] == "ok"
    far_alerts = (await db.execute(
        select(Alert).where(Alert.patient_id == pid, Alert.alert_type == "safe_zone_exit")
    )).scalars().all()
    assert far_alerts, "safe_zone_exit alert should fire when far from every known place"
    assert all(not a.resolved for a in far_alerts)

    # ── back near home -> the stale safe_zone_exit alert(s) auto-resolve ──────
    # Without this a caregiver opening the app later still gets the
    # full-screen SOS alert for an episode that's already over.
    back = (await get_risk(patient_id=pid, lat=near_lat, lng=near_lon, db=db)).model_dump()
    await db.commit()
    assert back["status"] == "ok"
    await db.refresh(far_alerts[0])
    resolved_alerts = (await db.execute(
        select(Alert).where(Alert.patient_id == pid, Alert.alert_type == "safe_zone_exit")
    )).scalars().all()
    assert resolved_alerts and all(a.resolved for a in resolved_alerts), (
        "safe_zone_exit alerts must auto-resolve once the patient is back in a known place"
    )

    # route_deviation now scales with real distance instead of a flat constant:
    # the far case's contribution must clearly exceed the near case's, and sit
    # at (or near) the weighted ceiling (0.30 * 100 = 30.0) once clamped.
    near_dev = near["contributions"]["route_deviation"]
    far_dev = far["contributions"]["route_deviation"]
    assert far_dev > near_dev, (near_dev, far_dev)
    assert far_dev >= 25.0, f"far route_deviation contribution should be near the 30-pt ceiling, got {far_dev}"


async def test_phase4_geofence_alert_auto_resolves_when_patient_leaves_danger_zone(db_session):
    """geofence (danger-zone entry) is a STATUS alert like safe_zone_exit: it
    must close itself once the patient is no longer inside the zone, or a
    caregiver keeps getting the full-screen SOS alert for a hazard the
    patient already walked away from.
    """
    db = db_session
    user = await crud.create_user(db, firebase_uid="geofence_resolve_test", name="Pat", role="patient")
    await db.flush()
    pid = user.id

    await _seed_normal_routine(db, pid)
    await analyze_behavior(db, pid, days=30)
    await _confirm_learned_places(db, pid)
    await db.commit()

    zone_lat, zone_lon = _offset(_PLACES[0][0], _PLACES[0][1], 3000.0, 3000.0)
    db.add(DangerZone(
        name="Test hazard @ geofence resolve test",
        center_latitude=zone_lat, center_longitude=zone_lon,
        radius_meters=150.0, zone_type="waterway", active=True,
        synthetic_injected=True,
        source_reference="test", rationale="isolated geofence-resolve check",
        created_by="test",
    ))
    await db.commit()

    # ── inside the danger zone -> geofence alert fires ─────────────────────
    inside = (await get_risk(patient_id=pid, lat=zone_lat, lng=zone_lon, db=db)).model_dump()
    await db.commit()
    assert inside["status"] == "ok"
    open_alerts = (await db.execute(
        select(Alert).where(Alert.patient_id == pid, Alert.alert_type == "geofence")
    )).scalars().all()
    assert open_alerts and all(not a.resolved for a in open_alerts)

    # ── back near home, well outside the zone -> the alert auto-resolves ───
    near_lat, near_lon = _offset(_PLACES[0][0], _PLACES[0][1], 20.0, 20.0)
    outside = (await get_risk(patient_id=pid, lat=near_lat, lng=near_lon, db=db)).model_dump()
    await db.commit()
    assert outside["status"] == "ok"
    resolved_alerts = (await db.execute(
        select(Alert).where(Alert.patient_id == pid, Alert.alert_type == "geofence")
    )).scalars().all()
    assert resolved_alerts and all(a.resolved for a in resolved_alerts), (
        "geofence alerts must auto-resolve once the patient leaves the danger zone"
    )


async def test_an_sos_raised_out_walking_closes_when_the_patient_gets_somewhere_safe(
        db_session):
    """"sos" ends with the episode; "sos_home" does not.

    A press from the navigation screen means "I am out and I need help", so
    reaching somewhere they know is the end of it. A press from the home
    screen means "something is wrong here", where being somewhere familiar
    says nothing at all about whether they are alright — that one waits for a
    person, which is why it is a separate type.
    """
    db = db_session
    user = await crud.create_user(db, firebase_uid="sos_resolve_test", name="Pat", role="patient")
    await db.flush()
    pid = user.id

    await _seed_normal_routine(db, pid)
    await analyze_behavior(db, pid, days=30)
    await _confirm_learned_places(db, pid)
    await db.commit()

    for alert_type in ("sos", "sos_home"):
        alert = await crud.save_alert(db, pid, alert_type=alert_type, severity="critical",
                                      message="Patient pressed the SOS button.")
        # Raised a while ago: this test is about an episode that ran and then
        # ended. One raised seconds ago is a different case, and the test below
        # is the one that pins it.
        alert.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db.commit()

    # Home, well inside the first known place.
    near_lat, near_lon = _offset(_PLACES[0][0], _PLACES[0][1], 20.0, 20.0)
    scored = (await get_risk(patient_id=pid, lat=near_lat, lng=near_lon, db=db)).model_dump()
    await db.commit()
    assert scored["status"] == "ok"

    rows = (await db.execute(select(Alert).where(Alert.patient_id == pid))).scalars().all()
    by_type = {a.alert_type: a for a in rows}
    assert by_type["sos"].resolved is True, "an SOS from out walking ends when they get somewhere safe"
    assert by_type["sos_home"].resolved is False, (
        "an SOS pressed at home must wait for a person — being at home is "
        "exactly where it was raised"
    )


async def test_a_just_pressed_sos_survives_the_next_scoring_round(db_session):
    """An SOS raised inside a familiar place must not close itself instantly.

    "They reached somewhere they know" is the end of an episode only if it was
    ever untrue. A patient can press SOS standing in their own garden, or
    walking past the market — and then the condition holds from the first
    second, the next scoring round resolves the row, and the caregiver's
    full-screen alert opens and vanishes while they are looking at it. That is
    what the grace period in risk.py::SOS_AUTO_RESOLVE_GRACE_S prevents.
    """
    db = db_session
    user = await crud.create_user(db, firebase_uid="sos_fresh_test", name="Pat",
                                  role="patient")
    await db.flush()
    pid = user.id

    await _seed_normal_routine(db, pid)
    await analyze_behavior(db, pid, days=30)
    await _confirm_learned_places(db, pid)
    await db.commit()

    pressed = await crud.save_alert(db, pid, alert_type="sos", severity="critical",
                                    message="Patient pressed the SOS button.")
    await db.commit()

    # Scored where they already were — inside the first known place.
    near_lat, near_lon = _offset(_PLACES[0][0], _PLACES[0][1], 20.0, 20.0)
    await get_risk(patient_id=pid, lat=near_lat, lng=near_lon, db=db)
    await db.commit()
    await db.refresh(pressed)
    assert pressed.resolved is False, (
        "an SOS pressed seconds ago was closed by the very next scoring round"
    )

    # Once the episode has had time to be over, the row does clear itself —
    # nobody should have to run SQL to get the screen back.
    pressed.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db.commit()
    await get_risk(patient_id=pid, lat=near_lat, lng=near_lon, db=db)
    await db.commit()
    await db.refresh(pressed)
    assert pressed.resolved is True
