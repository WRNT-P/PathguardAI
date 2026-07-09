"""Inject a synthetic PACING wandering segment for one patient (issue #1 Phase 2.5).

Pacing = repeated back-and-forth between two nearby points — the one literature
pattern implemented in this pass (lapping / random / direct are future work).

Design (to trip Module 2's Isolation Forest and Module 3's risk factors):
  * placed at an UNFAMILIAR anchor, offset far from every learned known-place
    cluster -> familiarity 0 -> unfamiliarity 1.0, and off any predicted route,
  * two points ~``--leg-m`` metres apart, oscillated ``--laps`` times,
  * low walking speed (~``--speed`` m/s) with a point every ``--step-s`` seconds
    -> low avg_speed + tiny movement radius + many >45 deg direction reversals,
  * timestamped to END ~now (within the last day) so Module 2's days=1 "current
    session" window sees it, with the real track ending ~2 days earlier.

Rows are flagged ``synthetic_injected=True`` so real vs injected stays auditable.
Requires the patient to already have real GPS + a behavioral profile (Phases 2-3).

Run:  python -m scripts.inject_wandering --patient 13
"""
import argparse
import asyncio
import json
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.database import AsyncSessionLocal
from app.db.models import BehavioralProfile, DangerZone, GPSData
from scripts.import_geolife import bearing_deg, haversine_m

_DZ_NAME_FMT = "Demo hazard @ patient {pid} wandering site"

_M_PER_DEG_LAT = 111_320.0


def _offset(lat: float, lon: float, dnorth_m: float, deast_m: float) -> tuple[float, float]:
    """Shift a lat/lon by metres north/east (small-offset flat-earth approx)."""
    dlat = dnorth_m / _M_PER_DEG_LAT
    dlon = deast_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def _known_place_centroids(profile: BehavioralProfile) -> list[tuple[float, float]]:
    if not profile or not profile.known_places:
        return []
    return [(p["latitude"], p["longitude"]) for p in json.loads(profile.known_places)]


def _min_dist_to_places(lat: float, lon: float, places) -> float:
    return min((haversine_m(lat, lon, plat, plon) for plat, plon in places), default=1e9)


def build_pacing(anchor_lat, anchor_lon, leg_m, laps, speed, step_s, end_at,
                 dwell_pts, dwell_step_s):
    """Generate a wandering episode ending at ``end_at``: [(lat, lon, dt), ...].

    Two phases: (1) pacing A<->B (many >45 deg reversals, low speed -> wandering),
    then (2) a stationary dwell at A with tiny jitter (speed ~0 -> the last few
    points read as "stopped", so Module 3's confusion classifier engages).
    """
    a = (anchor_lat, anchor_lon)
    b = _offset(anchor_lat, anchor_lon, leg_m, 0.0)  # point B, leg_m north of A
    pts_per_leg = max(2, int(leg_m / (speed * step_s)))

    seq = []  # (lat, lon, seconds_after_prev)
    for lap in range(laps):
        for frm, to in ((a, b), (b, a)):
            for k in range(pts_per_leg):
                f = k / pts_per_leg
                seq.append((frm[0] + (to[0] - frm[0]) * f,
                            frm[1] + (to[1] - frm[1]) * f, step_s))
    # dwell: stand ~still at A, ±2 m jitter, one point every dwell_step_s seconds
    for j in range(dwell_pts):
        jit = _offset(a[0], a[1], 2.0 if j % 2 else -2.0, -2.0 if j % 2 else 2.0)
        seq.append((jit[0], jit[1], dwell_step_s))

    # walk timestamps backwards from end_at using each point's own step
    dts = [end_at]
    for _, _, step in reversed(seq[1:]):
        dts.append(dts[-1] - timedelta(seconds=step))
    dts.reverse()
    return [(lat, lon, dt) for (lat, lon, _), dt in zip(seq, dts)]


def to_gps_rows(pacing, patient_id):
    objs, prev = [], None
    for lat, lon, dt in pacing:
        if prev is None:
            speed, direction = 0.0, None
        else:
            plat, plon, pdt = prev
            dsec = (dt - pdt).total_seconds()
            speed = haversine_m(plat, plon, lat, lon) / dsec if dsec > 0 else 0.0
            direction = bearing_deg(plat, plon, lat, lon)
        objs.append(GPSData(
            patient_id=patient_id, latitude=lat, longitude=lon,
            altitude=None, speed=speed, direction=direction,
            accuracy=None, device_motion=None,
            smooth_latitude=None, smooth_longitude=None,
            synthetic_injected=True, recorded_at=dt,
        ))
        prev = (lat, lon, dt)
    return objs


async def inject(patient_id, leg_m, laps, speed, step_s, offset_m, end_gap_min,
                 dwell_pts, dwell_step_s, skip_danger_zone, dz_radius):
    async with AsyncSessionLocal() as s:
        profile = await s.scalar(
            select(BehavioralProfile).where(BehavioralProfile.patient_id == patient_id))
        latest = await s.scalar(
            select(GPSData).where(GPSData.patient_id == patient_id,
                                  GPSData.synthetic_injected == False)  # noqa: E712
            .order_by(GPSData.recorded_at.desc()).limit(1))
        if latest is None:
            raise SystemExit(f"patient {patient_id} has no real GPS — run import first")

        places = _known_place_centroids(profile)
        # Anchor: offset NE from the last real position until it is clearly
        # unfamiliar (far from every known-place cluster). Fail-fast if we can't.
        anchor_lat, anchor_lon = _offset(latest.latitude, latest.longitude, offset_m, offset_m)
        d = _min_dist_to_places(anchor_lat, anchor_lon, places)
        print(f"anchor {anchor_lat:.5f},{anchor_lon:.5f}  dist-to-nearest-known-place = {d:.0f} m")
        if places and d < 500:
            raise SystemExit(
                f"anchor only {d:.0f} m from a known place — not unfamiliar enough; "
                f"raise --offset-m (issue #1: no silent fallback).")

        # wipe any previous injection so re-runs are idempotent
        await s.execute(delete(GPSData).where(GPSData.patient_id == patient_id,
                                              GPSData.synthetic_injected == True))  # noqa: E712

        end_at = datetime.now(timezone.utc) - timedelta(minutes=end_gap_min)
        pacing = build_pacing(anchor_lat, anchor_lon, leg_m, laps, speed, step_s, end_at,
                              dwell_pts, dwell_step_s)
        objs = to_gps_rows(pacing, patient_id)
        s.add_all(objs)

        # A geofenced hazard at the wandering site (realistic: the patient paced
        # off into an unfamiliar canal/waterway edge). Triggered by the injected
        # segment's own location, so it stays traceable to it. Idempotent by name.
        if not skip_danger_zone:
            dz_name = _DZ_NAME_FMT.format(pid=patient_id)
            await s.execute(delete(DangerZone).where(DangerZone.name == dz_name))
            s.add(DangerZone(
                name=dz_name,
                center_latitude=anchor_lat, center_longitude=anchor_lon,
                radius_meters=dz_radius, zone_type="waterway", active=True,
                synthetic_injected=True,  # never mistake this for a real KB hazard
                source_reference="issue #1 Phase 2.5 (synthetic demo scenario)",
                rationale="Open-water hazard co-located with the unfamiliar site where the "
                          "patient paced and stopped, disoriented — a realistic emergency, "
                          "and the factor that lifts a genuinely-high case over the alert line.",
                created_by="inject_wandering.py",
            ))

        await s.commit()

    span_min = (objs[-1].recorded_at - objs[0].recorded_at).total_seconds() / 60
    dz = "off" if skip_danger_zone else f"on (r={dz_radius:.0f}m)"
    print(f"injected {len(objs)} points (pacing+dwell) over ~{span_min:.0f} min, "
          f"ending {end_gap_min} min ago; danger-zone {dz}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", type=int, default=13)
    ap.add_argument("--leg-m", type=float, default=40.0, help="distance between the two pacing points")
    ap.add_argument("--laps", type=int, default=40, help="number of back-and-forth round trips")
    ap.add_argument("--speed", type=float, default=0.4, help="walking speed m/s")
    ap.add_argument("--step-s", type=int, default=15, help="seconds between points")
    ap.add_argument("--offset-m", type=float, default=3000.0, help="metres NE of last real point (unfamiliar area)")
    ap.add_argument("--end-gap-min", type=int, default=1, help="end the pacing this many minutes before now")
    ap.add_argument("--dwell-pts", type=int, default=5, help="stationary 'confused stop' points appended after pacing")
    ap.add_argument("--dwell-step-s", type=int, default=60, help="seconds between dwell points")
    ap.add_argument("--skip-danger-zone", action="store_true", help="do NOT seed a hazard at the wandering site")
    ap.add_argument("--dz-radius", type=float, default=300.0, help="danger-zone radius (m)")
    args = ap.parse_args()
    asyncio.run(inject(args.patient, args.leg_m, args.laps, args.speed,
                       args.step_s, args.offset_m, args.end_gap_min,
                       args.dwell_pts, args.dwell_step_s,
                       args.skip_danger_zone, args.dz_radius))


if __name__ == "__main__":
    main()
