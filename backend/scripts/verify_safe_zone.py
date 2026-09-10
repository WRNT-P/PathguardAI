# pathguard/backend/scripts/verify_safe_zone.py
"""Verify the safe-zone boundary rule on N positions, printing as it goes.

WHAT THIS IS -- AND WHAT IT IS NOT
----------------------------------
This is a **correctness check**, not an accuracy measurement. The rule under
test is one line of production code::

    outside_safe_zone = bool(known_places) and cluster_id is None
                                            (risk_data_collection.py:262)

``cluster_id`` comes from testing the position against **each pin's own
radius**. There is no model and no guessing here: a position is either inside a
circle or it is not. So the expected result is **100%**, and anything less is a
bug, not a limitation.

Do not report this as "detection accuracy 96%". A number below 100 here would
mean the arithmetic is wrong. Report it as: *the boundary rule was verified on
N positions, including the edge cases below, and classified every one
correctly.*

THE THREE CASES THAT ARE WORTH TESTING
--------------------------------------
Plain in/out is arithmetic. These three have each been a real bug:

1. **Each pin uses its OWN radius.** A hospital campus is not 150 m. The radius
   used to be hardcoded, so a wide pin reported its own grounds as "outside".
2. **A patient with NO pins is not "outside".** ``bool(known_places)`` guards
   this. Without it every patient with an empty profile false-alarms forever.
3. **The nearest pin is chosen among those whose radius the point is in**, not
   the nearest pin overall -- otherwise a close-but-small pin shadows a
   further-away pin that genuinely contains the point.

Run:  python -m scripts.verify_safe_zone --count 100
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.ai.module2_prediction.cluster_matcher import haversine_km
from app.ai.module3_risk.risk_data_collection import collect_risk_factors

_OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "evidence"

# Three pins with deliberately different radii -- see case 1 above.
PINS = [
    {"cluster_id": 0, "place_name": "home",   "latitude": 13.75630, "longitude": 100.50180,
     "radius_m": 150, "visit_frequency": 100},
    {"cluster_id": 1, "place_name": "temple", "latitude": 13.76400, "longitude": 100.50900,
     "radius_m": 400, "visit_frequency": 40},
    {"cluster_id": 2, "place_name": "clinic", "latitude": 13.74800, "longitude": 100.49300,
     "radius_m": 80,  "visit_frequency": 10},
]

_GREEN, _RED, _DIM, _BOLD, _OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _offset(lat: float, lng: float, north_m: float, east_m: float) -> tuple[float, float]:
    dlat = north_m / 111_320.0
    dlng = east_m / (111_320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lng + dlng


def _classify(lat: float, lng: float, pins: list[dict]) -> bool:
    """Ask the PRODUCTION function. Returns True when it says 'outside'."""
    raw = collect_risk_factors(
        gps_30d=[], recent_gps=[],
        profile={"known_places": json.dumps(pins)},
        current_lat=lat, current_lng=lng, danger_zones=[],
    )
    return bool(raw["outside_safe_zone"])


def _nearest_pin_distance_m(lat: float, lng: float, pins: list[dict]) -> tuple[str, float]:
    best, bestd = "-", float("inf")
    for p in pins:
        d = haversine_km(lat, lng, p["latitude"], p["longitude"]) * 1000
        if d < bestd:
            best, bestd = p["place_name"], d
    return best, bestd


def _cases(count: int, rng: random.Random) -> list[dict]:
    """Half inside a pin, half outside every pin, plus the three edge cases."""
    out: list[dict] = []
    half = count // 2

    for i in range(half):
        pin = PINS[i % len(PINS)]
        # Anywhere strictly inside this pin's own radius.
        r = rng.uniform(0, pin["radius_m"] * 0.9)
        ang = rng.uniform(0, 2 * math.pi)
        lat, lng = _offset(pin["latitude"], pin["longitude"],
                           r * math.cos(ang), r * math.sin(ang))
        out.append({"lat": lat, "lng": lng, "expected_outside": False,
                    "why": f"inside {pin['place_name']} (r={pin['radius_m']}m)",
                    "pins": PINS})

    # "Far from home" is NOT the same as "outside every pin": a point 1.5 km
    # from home towards the temple lands near the temple's 400 m radius. Reject
    # any candidate that falls inside a pin instead of assuming distance from
    # home is enough -- otherwise the harness invents a failure the code did
    # not commit, which on a stage is worse than no test at all.
    made = 0
    while made < count - half:
        r = rng.uniform(1500, 5000)
        ang = rng.uniform(0, 2 * math.pi)
        lat, lng = _offset(PINS[0]["latitude"], PINS[0]["longitude"],
                           r * math.cos(ang), r * math.sin(ang))
        if any(haversine_km(lat, lng, p["latitude"], p["longitude"]) * 1000
               <= p["radius_m"] for p in PINS):
            continue
        out.append({"lat": lat, "lng": lng, "expected_outside": True,
                    "why": "outside every pin", "pins": PINS})
        made += 1

    rng.shuffle(out)

    # ── the three cases that have each been a real bug ────────────────────────
    wide = PINS[1]
    lat, lng = _offset(wide["latitude"], wide["longitude"], 300, 0)   # 300 m out
    out.append({"lat": lat, "lng": lng, "expected_outside": False, "pins": PINS,
                "why": "EDGE 1: 300 m from a 400 m pin -- own radius, not a global 150 m"})

    out.append({"lat": PINS[0]["latitude"], "lng": PINS[0]["longitude"],
                "expected_outside": False, "pins": [],
                "why": "EDGE 2: patient has NO pins -- must NOT read as outside"})

    # A small pin 220 m north of home, and the position 120 m north of home --
    # so it sits 100 m from the small pin and 120 m from home. The SMALL pin is
    # the nearest, but the point is outside its 80 m radius and inside home's
    # 150 m one. An implementation that picks the nearest pin first and only
    # then checks the radius answers "outside" here; the correct answer is
    # "inside". This is the ordering CLAUDE.md records as a fixed bug.
    shop = {"cluster_id": 3, "place_name": "shop", "radius_m": 80, "visit_frequency": 5}
    shop["latitude"], shop["longitude"] = _offset(
        PINS[0]["latitude"], PINS[0]["longitude"], 220, 0)
    edge_lat, edge_lng = _offset(PINS[0]["latitude"], PINS[0]["longitude"], 120, 0)
    out.append({"lat": edge_lat, "lng": edge_lng, "expected_outside": False,
                "pins": PINS + [shop],
                "why": "EDGE 3: inside home(150m) at 120m, nearer shop(80m) at 100m"})

    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quiet", action="store_true", help="summary only")
    args = ap.parse_args()

    colour = sys.stdout.isatty()
    g, r_, d, b, off = (_GREEN, _RED, _DIM, _BOLD, _OFF) if colour else ("",) * 5

    cases = _cases(args.count, random.Random(args.seed))

    print(f"\n{b}PathGuard - Safe Zone Boundary Verification{off}")
    print(f"{d}rule: outside_safe_zone = bool(known_places) and cluster_id is None"
          f"   (risk_data_collection.py:262){off}")
    print(f"{d}pins: " + " | ".join(
        f"{p['place_name']} r={p['radius_m']}m" for p in PINS) + f"{off}\n")
    if not args.quiet:
        print(f"  {'#':>3}  {'case':<52} {'nearest pin':>16}  {'expected':>8}  "
              f"{'system':>8}   result")
        print(f"  {'-' * 3}  {'-' * 52} {'-' * 16}  {'-' * 8}  {'-' * 8}   ------")

    rows, wrong, false_alarm, missed = [], 0, 0, 0
    for i, c in enumerate(cases, start=1):
        got = _classify(c["lat"], c["lng"], c["pins"])
        ok = got == c["expected_outside"]
        if not ok:
            wrong += 1
            if got:
                false_alarm += 1     # said outside while actually inside
            else:
                missed += 1          # said inside while actually outside
        pin_name, dist = _nearest_pin_distance_m(c["lat"], c["lng"], c["pins"] or PINS)
        exp = "OUTSIDE" if c["expected_outside"] else "INSIDE"
        sysx = "OUTSIDE" if got else "INSIDE"

        if not args.quiet:
            mark = f"{g}OK{off}" if ok else f"{r_}FAIL{off}"
            print(f"  {i:>3}  {c['why']:<52} {pin_name:>7} {dist:>7.0f}m  "
                  f"{exp:>8}  {sysx:>8}   {mark}", flush=True)

        rows.append({"index": i, "case": c["why"],
                     "latitude": round(c["lat"], 7), "longitude": round(c["lng"], 7),
                     "nearest_pin": pin_name, "distance_m": round(dist, 1),
                     "expected": exp, "system": sysx, "correct": int(ok)})

    total = len(cases)
    correct = total - wrong
    pct = 100 * correct / total
    band = g if wrong == 0 else r_
    print(f"\n  {b}{band}{correct}/{total} correct ({pct:.1f}%){off}"
          f"   false alarms {false_alarm}   missed exits {missed}")
    print(f"  {d}100% is the only passing result: this rule is geometry, not a model."
          f" Anything less is a bug.{off}\n")

    summary = {
        "measurement": "safe_zone_boundary_verification",
        "kind": "correctness check, NOT an accuracy measurement",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "rule_under_test": "outside_safe_zone = bool(known_places) and cluster_id is None",
        "source": "app/ai/module3_risk/risk_data_collection.py:262",
        "seed": args.seed,
        "positions_tested": total,
        "correct": correct,
        "wrong": wrong,
        "false_alarms_said_outside_while_inside": false_alarm,
        "missed_exits_said_inside_while_outside": missed,
        "accuracy_pct": round(pct, 2),
        "note": ("A position is either inside a circle or it is not, so 100% is "
                 "expected and required. Reporting a figure below 100 as 'detection "
                 "accuracy' would be describing a bug as a limitation."),
        "pins": PINS,
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUT_DIR / "safe_zone_verification.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    import csv
    with (_OUT_DIR / "safe_zone_positions.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    sys.exit(1 if wrong else 0)


if __name__ == "__main__":
    main()
