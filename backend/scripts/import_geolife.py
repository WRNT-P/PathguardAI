"""Import one GeoLife user's GPS trajectory into gps_data as a patient (issue #1 Phase 2).

GeoLife data is from 2007-2012, but every AI module reads only recent history
(`get_gps_history` days=30, and Module 2 "current" = days=1). So a straight import
of the 2008 timestamps would be invisible. This script therefore:

  1. reads the user's .plt files (skip 6 header lines each),
  2. keeps only the last ``--days`` days of the real track (the densest tail —
     the only slice the modules will ever read),
  3. downsamples to >= ``--min-interval`` seconds between points (a raw GeoLife
     user has ~600k 1-5s points; clustering does not need that resolution),
  4. shifts every timestamp by a single constant delta so the track ends at
     (now - ``--end-gap-days``) days, PRESERVING relative spacing so derived
     speed stays physically real, and leaving a gap at the end for Phase 2.5 to
     inject a "current session" wandering segment,
  5. derives ``speed`` = haversine/Δt (m/s, REQUIRED — Module 1 drops null-speed
     rows) and ``direction`` = bearing between consecutive kept points.

accuracy / device_motion / smooth_* are left NULL (no module reads them; Module 4
falls back to raw lat/lon). Real points get synthetic_injected=False.

Idempotent: re-running for the same user wipes that patient's old gps_data first.

Run:  python -m scripts.import_geolife --user 025
"""
import argparse
import asyncio
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select

from app.db.database import AsyncSessionLocal
from app.db.models import GPSData, User

_HEADER_LINES = 6
_FEET_TO_M = 0.3048
_ALT_INVALID = -777  # GeoLife sentinel for missing altitude


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two lat/lon points."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing (compass degrees 0-359.99) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _parse_plt(path: Path) -> list[tuple[float, float, float | None, datetime]]:
    """Parse one .plt file -> [(lat, lon, altitude_m|None, recorded_at_utc)]."""
    out = []
    with path.open() as fh:
        for i, line in enumerate(fh):
            if i < _HEADER_LINES:
                continue
            parts = line.strip().split(",")
            if len(parts) < 7:
                continue
            lat, lon = float(parts[0]), float(parts[1])
            alt_ft = float(parts[3])
            alt_m = None if alt_ft == _ALT_INVALID else alt_ft * _FEET_TO_M
            # GeoLife records local time; we attach UTC and keep it consistent.
            dt = datetime.strptime(f"{parts[5]} {parts[6]}", "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            out.append((lat, lon, alt_m, dt))
    return out


def load_points(user_dir: Path, days: int, min_interval: int):
    """Read all .plt files, keep last ``days``, downsample by ``min_interval`` seconds."""
    traj = user_dir / "Trajectory"
    rows = []
    for plt in sorted(traj.glob("*.plt")):
        rows.extend(_parse_plt(plt))
    rows.sort(key=lambda r: r[3])
    if not rows:
        return []

    cutoff = rows[-1][3] - timedelta(days=days)
    windowed = [r for r in rows if r[3] >= cutoff]

    kept = []
    last_kept_dt = None
    for r in windowed:
        if last_kept_dt is None or (r[3] - last_kept_dt).total_seconds() >= min_interval:
            kept.append(r)
            last_kept_dt = r[3]
    return kept


def build_gps_rows(kept, patient_id: int, end_gap_days: int):
    """Shift timestamps to recent, derive speed+direction, return GPSData objects."""
    now = datetime.now(timezone.utc)
    target_last = now - timedelta(days=end_gap_days)
    delta = target_last - kept[-1][3]

    objs = []
    prev = None
    for lat, lon, alt_m, dt in kept:
        rec_at = dt + delta
        if prev is None:
            speed, direction = 0.0, None
        else:
            plat, plon, pdt = prev
            dsec = (dt - pdt).total_seconds()
            dist = haversine_m(plat, plon, lat, lon)
            speed = dist / dsec if dsec > 0 else 0.0
            direction = bearing_deg(plat, plon, lat, lon)
        objs.append(GPSData(
            patient_id=patient_id,
            latitude=lat, longitude=lon,
            altitude=alt_m,
            speed=speed, direction=direction,
            accuracy=None, device_motion=None,
            smooth_latitude=None, smooth_longitude=None,
            synthetic_injected=False,
            recorded_at=rec_at,
        ))
        prev = (lat, lon, dt)
    return objs


async def import_user(user: str, data_root: Path, days: int, min_interval: int, end_gap_days: int):
    user_dir = data_root / user
    if not (user_dir / "Trajectory").is_dir():
        raise SystemExit(f"no Trajectory dir under {user_dir}")

    print(f"reading GeoLife user {user} ...")
    kept = load_points(user_dir, days, min_interval)
    if len(kept) < 50:
        raise SystemExit(
            f"only {len(kept)} points after windowing/downsample — data too thin, "
            f"stopping (issue #1: no auto-fallback). Pick another user or lower --min-interval."
        )
    span_days = (kept[-1][3] - kept[0][3]).total_seconds() / 86400
    print(f"  kept {len(kept)} points spanning {span_days:.1f} days (after downsample)")

    firebase_uid = f"geolife_{user}"
    async with AsyncSessionLocal() as s:
        existing = await s.scalar(select(User).where(User.firebase_uid == firebase_uid))
        if existing:
            patient_id = existing.id
            await s.execute(delete(GPSData).where(GPSData.patient_id == patient_id))
            print(f"  reusing patient id={patient_id}, wiped old gps_data")
        else:
            u = User(firebase_uid=firebase_uid, name=f"GeoLife {user}", role="patient")
            s.add(u)
            await s.flush()
            patient_id = u.id
            print(f"  created patient id={patient_id}")

        objs = build_gps_rows(kept, patient_id, end_gap_days)
        s.add_all(objs)
        await s.commit()

    print(f"done: imported {len(objs)} rows for patient {patient_id} "
          f"(ends ~{end_gap_days}d ago; speed derived, non-null)")
    return patient_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="025")
    ap.add_argument("--data-root", default="data/Geolife/Data")
    ap.add_argument("--days", type=int, default=30, help="last N days of the real track to import")
    ap.add_argument("--min-interval", type=int, default=20, help="downsample: min seconds between kept points")
    ap.add_argument("--end-gap-days", type=int, default=2, help="leave this many days at the end for Phase 2.5 injection")
    args = ap.parse_args()
    asyncio.run(import_user(
        args.user, Path(args.data_root), args.days, args.min_interval, args.end_gap_days,
    ))


if __name__ == "__main__":
    main()
