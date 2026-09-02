"""Phase 6 — how many days of tracking before the learned place set stops changing?

The app has to tell a new caregiver something honest on day one: "still
learning, N more days before full alerting". Until now the only answer anyone
had was "a couple of weeks", which was a guess. This measures it.

Method: take one GeoLife user, and for D = 1, 2, 3 ... run Module 1's *real*
pipeline (``preprocess_gps`` then ``cluster_places``, the same two calls
``analyze_behavior`` makes) over days 1..D. Compare each day's place set to the
set learned from the whole 30-day window — 30 because that is the only window
production ever reads (``crud.get_gps_history(days=30)``). N is the first day
whose set covers the reference and never falls back below it.

Coverage is weighted by ``visit_frequency``, not counted per place. A place the
patient spends their life in and a shop they entered once are one place each by
count, but they are worth wildly different amounts to the risk score, which
asks "is this spot familiar" of wherever the patient is standing right now. So
the number reported is the share of the patient's recorded time spent at places
the system already knows about.

Windows are measured at several different start dates, because "day 1" for a
real family is whichever day they install the app, not a day chosen for having
good data. The spread between those runs is part of the answer.

**GeoLife is not a dementia patient.** User 025 is a Beijing researcher carrying
a logger on trips, and the logger is off at home more often than not — the
opposite of a phone in a pocket all day. Read the result as an order of
magnitude for how fast DBSCAN settles on a routine, not as a number to print in
the UI unexamined. And per the standing rule: GeoLife teaches movement
parameters only, never a real patient's places.

What it found (user 025, 5 windows, 2026-08-22): coverage climbs to ~70% in the
first one to two weeks and then crawls — median 18 days to 80%, 26 days to 95%,
inside a window that is only 30 days long. So the place set does not settle
within the history production can see, and "wait N days" is not a plan. Pins
are: a patient with pins and no history scores within a point of one carrying a
week of trips (see plan §4b).

The more serious finding is what it learns. Run without ``--stops`` — Module 1's
actual behaviour — and a 30-day window yields 156 "places", 124 of them with an
average stay under five minutes, the largest spanning 1.5 km from its own
centroid. Those are red lights and roads, not destinations. See ``--stops``.

Run:  python -m scripts.measure_learning_days                    # as Module 1 is
      python -m scripts.measure_learning_days --stops            # with stop detection
      python -m scripts.measure_learning_days --windows 5 --coverage 0.8
"""
import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

from app.ai.module1_behavior.place_clustering import EARTH_RADIUS_M
from app.ai.module1_behavior.data_preprocessing import preprocess_gps
from app.ai.module1_behavior.place_clustering import cluster_places
from app.ai.module1_behavior.behavior_pipeline import gps_history_to_dataframe
from scripts.import_geolife import build_gps_rows, haversine_m, load_points

# The radius the rest of the system already uses to decide "same place"
# (app/api/places.py:DEFAULT_RADIUS_M, cluster_matcher's fallback).
DEFAULT_MATCH_RADIUS_M = 150.0

# Production reads exactly this much history — a longer reference window would
# measure a system that does not exist.
DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class StopParams:
    """Stay-point extraction settings — see ``extract_stay_points``."""
    radius_m: float = 100.0
    minutes: float = 15.0
    # DBSCAN min_samples once the input is stops rather than raw fixes, so this
    # reads as "visited at least this many times to count as a place". Module 1's
    # hardcoded 5 means five *readings*, which on a 20 s track is 100 seconds of
    # standing still — a different question entirely.
    min_visits: int = 2


def extract_stay_points(df: pd.DataFrame, radius_m: float,
                        minutes: float) -> pd.DataFrame:
    """Collapse the track to one row per *stop* — a PROPOSAL, not production.

    Module 1 currently hands every GPS point straight to DBSCAN, which is why a
    30-day window yields 156 "places" of which 124 have an average stay under
    five minutes: at eps = 50 m on a 20 s track, a red light is indistinguishable
    from a destination, and dense points chain along a road until one cluster
    spans 1.5 km. This is the standard fix (Li et al. stay-point detection): find
    runs of consecutive readings that stay inside ``radius_m`` for at least
    ``minutes``, and cluster those instead.

    Kept here rather than in ``app/ai/`` on purpose — changing Module 1's output
    shape is the Module 1 owner's call, and this script's job is to show whether
    it is worth making.
    """
    lat = df["latitude"].to_numpy()
    lng = df["longitude"].to_numpy()
    ts = df["timestamp"].to_numpy()
    n = len(df)

    stays, i = [], 0
    while i < n:
        j = i + 1
        while j < n and haversine_m(lat[i], lng[i], lat[j], lng[j]) <= radius_m:
            j += 1
        span_s = (ts[j - 1] - ts[i]) / pd.Timedelta(seconds=1)
        if span_s >= minutes * 60:
            stays.append({
                "latitude": float(lat[i:j].mean()),
                "longitude": float(lng[i:j].mean()),
                "speed": 0.0,
                "timestamp": ts[i],
            })
            i = j
        else:
            i += 1
    return pd.DataFrame(stays, columns=["latitude", "longitude", "speed", "timestamp"])


def cluster_stays(stays: pd.DataFrame, min_visits: int) -> list[dict]:
    """``cluster_places`` over stops, with min_samples exposed. Prototype only.

    Same DBSCAN, same 50 m eps, same output keys — the one thing that has to
    change is min_samples, because over stay points it counts visits, not GPS
    fixes, and 5 visits inside 30 days would discard everywhere but home.
    """
    eps = 50 / EARTH_RADIUS_M
    labels = DBSCAN(eps=eps, min_samples=min_visits, metric="haversine").fit(
        np.radians(stays[["latitude", "longitude"]].values)
    ).labels_

    out = []
    for cid in sorted(set(labels)):
        if cid == -1:
            continue
        rows = stays[labels == cid]
        out.append({
            "cluster_id": int(cid),
            "latitude": float(rows["latitude"].mean()),
            "longitude": float(rows["longitude"].mean()),
            "visit_frequency": len(rows),
            "avg_stay_time": 0.0,
        })
    return out


def _places_for(df: pd.DataFrame, stops: "StopParams | None" = None) -> list[dict]:
    """Module 1's real pipeline on one slice. Empty when there is too little data."""
    if len(df) < 5:                      # DBSCAN min_samples
        return []
    smoothed = preprocess_gps(df.copy())
    if stops is None:
        return cluster_places(smoothed)

    stayed = extract_stay_points(smoothed, stops.radius_m, stops.minutes)
    if len(stayed) < stops.min_visits:
        return []
    return cluster_stays(stayed, stops.min_visits)


def _matched(place: dict, reference: list[dict], radius_m: float) -> int | None:
    """Index of the reference place this one lands on, or None."""
    best, best_d = None, radius_m
    for i, ref in enumerate(reference):
        d = haversine_m(place["latitude"], place["longitude"],
                        ref["latitude"], ref["longitude"])
        if d <= best_d:
            best, best_d = i, d
    return best


def measure_window(df: pd.DataFrame, start: date, window_days: int,
                   radius_m: float, coverage_target: float,
                   stops: StopParams | None = None) -> dict:
    """Day-by-day coverage curve for one window, and the N it implies."""
    days = [start + timedelta(days=i) for i in range(window_days)]
    dates = df["timestamp"].dt.date

    reference = _places_for(df[(dates >= days[0]) & (dates <= days[-1])], stops)
    if not reference:
        return {"start": str(start), "n_days": None, "reference_places": 0,
                "note": "no places learned in the whole window"}

    total_weight = sum(p["visit_frequency"] for p in reference)
    top_ref = max(range(len(reference)), key=lambda i: reference[i]["visit_frequency"])

    curve, home_day = [], None
    for d_index, day in enumerate(days, start=1):
        slice_df = df[(dates >= days[0]) & (dates <= day)]
        found = _places_for(slice_df, stops)

        hit = {i for i in (_matched(p, reference, radius_m) for p in found)
               if i is not None}
        coverage = sum(reference[i]["visit_frequency"] for i in hit) / total_weight
        if home_day is None and top_ref in hit:
            home_day = d_index

        curve.append({
            "day": d_index,
            "points": int(len(slice_df)),
            "places_found": len(found),
            "reference_places_hit": len(hit),
            "coverage": round(coverage, 4),
        })

    # N: the first day that reaches the target and never drops below it again.
    n_days = None
    for i, row in enumerate(curve):
        if row["coverage"] >= coverage_target and all(
            r["coverage"] >= coverage_target for r in curve[i:]
        ):
            n_days = row["day"]
            break

    return {
        "start": str(start),
        "n_days": n_days,
        "home_found_on_day": home_day,
        "reference_places": len(reference),
        "curve": curve,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="025")
    ap.add_argument("--data-root", default="data/Geolife/Data")
    ap.add_argument("--min-interval", type=int, default=20,
                    help="downsample seconds — same default as import_geolife")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--windows", type=int, default=5,
                    help="how many differently-dated windows to measure")
    ap.add_argument("--match-radius", type=float, default=DEFAULT_MATCH_RADIUS_M)
    ap.add_argument("--coverage", type=float, default=0.95,
                    help="share of the patient's time that must be at known places")
    # NOTE 2026-09-02: `cluster_places` itself now does stay-point extraction
    # with these exact defaults (100m/15min/min_visits=2) — the fix this flag
    # prototyped is in production. With default values, --stops and no --stops
    # now measure the same pipeline. This flag still earns its keep for sweeping
    # non-default radius/minutes/min-visits values against the reference window.
    ap.add_argument("--stops", action="store_true",
                    help="use this script's own stay-point params instead of cluster_places' "
                         "defaults (only differs from no-flag when other --stop-* args are set)")
    ap.add_argument("--stop-radius", type=float, default=100.0)
    ap.add_argument("--stop-minutes", type=float, default=15.0)
    ap.add_argument("--min-visits", type=int, default=2,
                    help="visits before a stop counts as a place (--stops only)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    kept = load_points(Path(args.data_root) / args.user, days=100_000,
                       min_interval=args.min_interval)
    if not kept:
        raise SystemExit(f"no GeoLife points for user {args.user}")

    # build_gps_rows is what the importer feeds Postgres: derived speed (Module 1
    # drops null-speed rows) and timestamps shifted to be recent. Relative spacing
    # is preserved, so day boundaries stay where they were.
    df = gps_history_to_dataframe(build_gps_rows(kept, patient_id=0, end_gap_days=2))
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    first, last = df["timestamp"].dt.date.min(), df["timestamp"].dt.date.max()
    span = (last - first).days + 1
    print(f"user {args.user}: {len(df)} points, {first} -> {last} ({span} days)")
    stops = (StopParams(args.stop_radius, args.stop_minutes, args.min_visits)
             if args.stops else None)
    print(f"window {args.window_days} d - match {args.match_radius:.0f} m - "
          f"coverage target {args.coverage:.0%}")
    print("clustering: " + (
        f"STAY POINTS ({args.stop_radius:.0f} m / {args.stop_minutes:.0f} min / "
        f"{args.min_visits} visits) - proposal"
        if stops else "raw GPS points - what Module 1 does today") + "\n")

    latest_start = span - args.window_days
    if latest_start < 0:
        raise SystemExit(f"record is {span} days, shorter than the window")
    step = latest_start / max(args.windows - 1, 1)
    starts = sorted({first + timedelta(days=round(i * step))
                     for i in range(args.windows)})

    results = [measure_window(df, s, args.window_days, args.match_radius,
                              args.coverage, stops) for s in starts]

    print(f"{'start':<12}{'N':>5}{'home':>7}{'places':>9}   coverage by day 1..10")
    for r in results:
        n = "none" if r.get("n_days") is None else r["n_days"]
        home = r.get("home_found_on_day") or "-"
        curve = "".join(f"{row['coverage']:5.2f}" for row in r.get("curve", [])[:10])
        print(f"{r['start']:<12}{str(n):>5}{str(home):>7}"
              f"{r.get('reference_places', 0):>9}   {curve}")

    settled = [r["n_days"] for r in results if r.get("n_days")]
    homes = [r["home_found_on_day"] for r in results if r.get("home_found_on_day")]
    print()
    if settled:
        print(f"N (days until {args.coverage:.0%} of time is at known places): "
              f"min {min(settled)} - median {sorted(settled)[len(settled)//2]} - "
              f"max {max(settled)}  ({len(settled)}/{len(results)} windows settled)")
    else:
        print(f"no window reached {args.coverage:.0%} coverage and held it")
    if homes:
        print(f"most-visited place found on day: min {min(homes)} - max {max(homes)}"
              "   (this is the one route_deviation falls back to)")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
