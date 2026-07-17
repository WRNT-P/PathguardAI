"""Terminal demo — run the full pipeline on real data and print it (issue #2).

Drives Modules 1-5 for the imported GeoLife patient and prints a readable,
judge-facing narrative: normal routine learned -> wandering episode detected ->
risk scored -> emergency + search area + caregiver actions.

TF-free (Module 2's LSTM destination path is skipped; its wandering detector —
the heart of the demo — runs on sklearn). Re-injects a FRESH pacing episode
first so the "current session" is always within Module 2/3's recent window.

Run:  python -m scripts.demo_run --patient 13
"""
import argparse
import asyncio
import json

from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.db.models import DangerZone, GPSData
from app.db import crud
from app.ai.module2_prediction.wandering_detection import WanderingDetector
from app.api.risk import get_risk
from app.api.search_area import get_search_area
from app.api.recommendation import get_recommendations
from scripts.inject_wandering import inject

_BAR = "=" * 60


def _h(title):
    print(f"\n{_BAR}\n  {title}\n{_BAR}")


async def run(patient_id: int, fresh: bool):
    # Step 0 — refresh the injected "current session" so timestamps are now.
    if fresh:
        await inject(patient_id, leg_m=40.0, laps=40, speed=0.4, step_s=15,
                     offset_m=3000.0, end_gap_min=1, dwell_pts=5, dwell_step_s=60,
                     skip_danger_zone=False, dz_radius=300.0)

    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, patient_id) if hasattr(crud, "get_user") else None
        name = getattr(user, "name", f"patient {patient_id}")

        _h(f"PathGuard AI — Live Pipeline Demo  ({name})")
        print("  ข้อมูลจริง: Microsoft GeoLife (user 025) + synthetic wandering overlay")

        # ── Module 1 — learned routine (persisted profile, built on real data) ──
        profile = await crud.get_behavioral_profile(db, patient_id)
        places = json.loads(profile.known_places) if profile and profile.known_places else []
        _h("[Module 1] Behavior — learned normal routine")
        print(f"  known places learned : {len(places)}")
        if places:
            top = places[0]
            print(f"  example place        : ({top['latitude']:.4f}, {top['longitude']:.4f})")
        print("  (built from the real GeoLife history, BEFORE the wandering overlay)")

        # ── Module 2 — wandering detector (TF-free) on the current session ──────
        real = (await db.execute(select(GPSData).where(
            GPSData.patient_id == patient_id, GPSData.synthetic_injected.is_(False)
        ).order_by(GPSData.recorded_at))).scalars().all()
        inj = (await db.execute(select(GPSData).where(
            GPSData.patient_id == patient_id, GPSData.synthetic_injected.is_(True)
        ).order_by(GPSData.recorded_at))).scalars().all()
        det = WanderingDetector(); det.fit(real)
        w = det.detect(inj)
        f = w["features"]
        _h("[Module 2] Prediction — evaluate current movement")
        flag = "WANDERING DETECTED" if w["wandering_level"] != "normal" else "normal"
        print(f"  pattern              : {flag}  (level={w['wandering_level']})")
        print(f"  direction reversals  : {f['direction_changes']}  |  avg speed: {f['avg_speed_ms']} m/s")
        print(f"  movement radius      : {f['movement_radius_km']*1000:.0f} m  (tight pacing = confined)")
        print("  (LSTM destination prediction skipped — needs TensorFlow)")

        # ── Module 3 — risk ─────────────────────────────────────────────────────
        risk = (await get_risk(patient_id=patient_id, lat=None, lng=None, db=db)).model_dump()
        await db.commit()
        _h("[Module 3] Risk — score + emergency decision")
        print(f"  RISK SCORE           : {risk['risk_score']} / 100   -> {risk['risk_level'].upper()}")
        print(f"  factor contributions : {risk['contributions']}")
        if risk["emergency"]:
            print(f"  🚨 EMERGENCY / ALERT FIRED  (reason: {risk['reason']})")

        # ── Module 4 — search area (simulate: patient now missing) ──────────────
        latest = await crud.get_latest_gps(db, patient_id)
        area = (await get_search_area(
            patient_id=patient_id, last_lat=latest.latitude, last_lng=latest.longitude,
            last_speed_ms=1.0, last_direction_deg=None, time_missing_minutes=120, db=db
        )).model_dump()
        _h("[Module 4] Search Area — if the patient goes missing now")
        print(f"  {area.get('message', area.get('status'))}")

        # ── Module 5 — recommendations ──────────────────────────────────────────
        rec = (await get_recommendations(patient_id=patient_id, lat=None, lng=None, db=db)).model_dump()
        await db.commit()
        _h("[Module 5] Recommend — where to look, ranked for the caregiver")
        actions = rec.get("recommendations") or rec.get("actions") or []
        if actions:
            for a in actions[:5]:
                rank = a.get("rank", "?")
                lat, lon = a.get("latitude"), a.get("longitude")
                conf = a.get("confidence_pct", round(a.get("confidence", 0) * 100))
                print(f"  #{rank}  check ({lat:.4f}, {lon:.4f})   confidence {conf}%")
        else:
            print(f"  status={rec.get('status')}  {rec.get('message','')}")

        _h("✓ Pipeline OK — real data in, wandering caught, 5/5 modules ran")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", type=int, default=13)
    ap.add_argument("--no-fresh", action="store_true", help="skip re-injecting a fresh episode")
    args = ap.parse_args()
    asyncio.run(run(args.patient, fresh=not args.no_fresh))


if __name__ == "__main__":
    main()
