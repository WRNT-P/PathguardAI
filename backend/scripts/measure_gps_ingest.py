# pathguard/backend/scripts/measure_gps_ingest.py
"""Send N GPS points at POST /api/gps and measure what actually arrives.

WHAT IS BEING MEASURED
----------------------
delivery rate : points sent vs rows that actually appear in ``gps_data``. The
                count comes from the database, not from counting HTTP 200s --
                a 200 the row never followed would otherwise pass unnoticed.
latency       : per-request wall clock, reported as percentiles. A mean hides
                the slow tail, and the slow tail is what a caregiver feels.

THIS WRITES TO THE DATABASE
---------------------------
Every point becomes a real row. ``--throwaway`` creates a patient for the run
and deletes it in a ``finally`` block, so a real patient is never written to and
nothing is left behind even if the run crashes -- prefer it. ``--patient <id>``
is the manual alternative; clean up after it with::

    python -m scripts.delete_patient --user-id <id>             # report only
    python -m scripts.delete_patient --user-id <id> --confirm   # delete

The script refuses to run with neither: it will not guess which patient to write
a hundred rows to.

TWO MODES
---------
default          : drives the real FastAPI app in-process over ASGI, against
                   the real database, with ONLY the token check replaced. The
                   caller is authenticated as the patient -- which is what a
                   paired handset is -- so authorization still runs for real.
                   No token needed, no server needed. Latency here excludes the
                   phone-to-server hop but INCLUDES the server-to-Neon round
                   trips, and those dominate: Neon is remote.
--base-url+--token : real HTTP against a running server with a real Firebase
                   token. Slower to set up, and the number then includes the
                   network, which is what a phone actually pays.

Run:  python -m scripts.measure_gps_ingest --throwaway --count 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics as st
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import os

# Before importing app.db.database: the engine reads DEBUG at import time and
# turns on SQL echo, which buries the per-request lines this script exists to
# show. Clearing it here changes only this process.
os.environ["DEBUG"] = ""

import httpx  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db.database import AsyncSessionLocal  # noqa: E402
from app.db.models import GPSData  # noqa: E402

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

_OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "evidence"

# A short walk around one point, so the payloads are plausible rather than
# identical -- identical coordinates would let a cache or a dedupe hide a fault.
_BASE_LAT, _BASE_LNG = 13.7563, 100.5018   # Bangkok


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _stats(ms: list[float]) -> dict:
    s = sorted(ms)
    def pct(p: float) -> float:
        return s[min(len(s) - 1, max(0, int(round(p / 100 * len(s))) - 1))]
    return {
        "n": len(s), "min_ms": round(s[0], 2), "p50_ms": round(st.median(s), 2),
        "p95_ms": round(pct(95), 2), "p99_ms": round(pct(99), 2),
        "max_ms": round(s[-1], 2), "mean_ms": round(st.mean(s), 2),
        "stdev_ms": round(st.stdev(s), 2) if len(s) > 1 else 0.0,
    }


async def _count_rows(patient_id: int) -> int:
    async with AsyncSessionLocal() as s:
        return int((await s.execute(
            select(func.count()).select_from(GPSData)
            .where(GPSData.patient_id == patient_id)
        )).scalar_one())


def _points(patient_id: int, count: int) -> list[dict]:
    start = datetime.now(timezone.utc) - timedelta(seconds=5 * count)
    out = []
    for i in range(count):
        out.append({
            "patient_id": patient_id,
            # ~1.1 m per step north, ~0.4 m east; a slow walk, not a teleport
            "latitude": round(_BASE_LAT + i * 0.00001, 7),
            "longitude": round(_BASE_LNG + i * 0.000004, 7),
            "accuracy": 8.0,
            "speed": 1.2,
            "recorded_at": (start + timedelta(seconds=5 * i)).isoformat(),
        })
    return out


def _build_client(base_url: str | None, token: str | None, patient_id: int):
    """Real HTTP if a base_url is given, otherwise the app itself over ASGI."""
    if base_url:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0), "http"

    from app.db.database import init_firebase
    from app.main import app
    from app.services.auth import Caller, current_caller

    # httpx's ASGI transport does not run the lifespan, so without this the
    # Firebase live-update write inside POST /api/gps fails on every request
    # and the measured latency would be missing a real production cost.
    init_firebase()

    # Authenticated AS THE PATIENT, which is what a paired handset is: it posts
    # its own position. Signing in as nobody would be rejected by
    # ``assert_may_access_patient`` with 403 -- and that rejection is correct,
    # so the access-control path stays exercised rather than bypassed. Only the
    # token check is replaced; the database, the Kalman filter and the risk
    # trigger are all the production ones.
    app.dependency_overrides[current_caller] = lambda: Caller(
        user_id=patient_id, firebase_uid=f"perftest-{patient_id}", authenticated=True
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://asgi"), "asgi"


async def _create_throwaway() -> int:
    """A patient that exists only for this run. Deleted in the finally block."""
    from sqlalchemy import text
    from app.db.database import engine
    uid = f"PERFTEST-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    async with engine.begin() as conn:
        return int(await conn.scalar(
            text("INSERT INTO users (firebase_uid, name, role) "
                 "VALUES (:uid, :name, 'patient') RETURNING id"),
            {"uid": uid, "name": f"PERFTEST throwaway {uid}"},
        ))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", type=int, default=None,
                    help="THROWAWAY patient id -- this writes real rows")
    ap.add_argument("--throwaway", action="store_true",
                    help="create a patient for this run and delete it afterwards, "
                         "so no real patient is ever written to")
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--base-url", default=None, help="e.g. http://127.0.0.1:8000")
    ap.add_argument("--token", default=None, help="Firebase ID token, with --base-url")
    ap.add_argument("--quiet", action="store_true", help="summary only, no live lines")
    args = ap.parse_args()

    if not args.patient and not args.throwaway:
        ap.error("pass --throwaway (recommended) or --patient <id>. "
                 "This writes real rows, so it will not guess which patient you meant.")
    created = None
    if args.throwaway:
        created = args.patient = await _create_throwaway()
        print(f"created throwaway patient {created} -- it will be deleted at the end")

    try:
        await _run(args)
    finally:
        if created is not None:
            print(f"\ncleaning up throwaway patient {created}:")
            from scripts.delete_patient import main as delete_main
            await delete_main(created, confirm=True)


async def _run(args) -> None:
    before = await _count_rows(args.patient)
    payloads = _points(args.patient, args.count)
    client, mode = _build_client(args.base_url, args.token, args.patient)

    colour = sys.stdout.isatty()
    green, red, dim, bold, off = (
        ("\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m") if colour else ("",) * 5)

    if not args.quiet:
        print(f"\n{bold}POST /api/gps  x{args.count}   patient={args.patient}   "
              f"mode={mode}{off}")
        print(f"{dim}  #    status   latency   note{off}")
        print(f"{dim}  ---  ------  --------   ----{off}")

    ms, codes, first_error = [], {}, None
    async with client:
        for i, p in enumerate(payloads, start=1):
            t0 = time.perf_counter()
            try:
                r = await client.post("/api/gps", json=p)
                code = r.status_code
                if code >= 400 and first_error is None:
                    first_error = f"HTTP {code}: {r.text[:200]}"
            except Exception as exc:
                code = -1
                if first_error is None:
                    first_error = f"{type(exc).__name__}: {exc}"
            took = (time.perf_counter() - t0) * 1000
            ms.append(took)
            codes[code] = codes.get(code, 0) + 1

            if not args.quiet:
                ok = code in (200, 201)
                # A request far slower than the running median is the one that
                # carried a risk-scoring round; scoring is throttled to once a
                # minute per patient, so a handful of these is expected and is
                # worth showing rather than hiding in a percentile.
                med = st.median(ms)
                slow = took > max(3 * med, 1500) and len(ms) > 5
                print(f"  {i:>3}  {(green if ok else red)}{code:>6}{off}  "
                      f"{took:>7.0f}ms   "
                      f"{(bold + 'AI scoring round' + off) if slow else ''}", flush=True)

    after = await _count_rows(args.patient)
    landed = after - before
    accepted = codes.get(200, 0) + codes.get(201, 0)

    result = {
        "measurement": "gps_ingest",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "mode": mode,
        "note": ("ASGI in-process: real app, real database, only the token check "
                 "replaced -- the caller is authenticated as the patient, so the "
                 "access-control path still runs. Latency excludes the phone-to-server "
                 "hop but INCLUDES the server-to-Neon round trips, which dominate."
                 if mode == "asgi" else
                 "Real HTTP against a running server. Latency includes the network."),
        "patient_id": args.patient,
        "points_sent": args.count,
        "http_accepted": accepted,
        "rows_before": before,
        "rows_after": after,
        "rows_landed": landed,
        "delivery_rate_pct": round(100 * landed / args.count, 2) if args.count else 0.0,
        "accept_rate_pct": round(100 * accepted / args.count, 2) if args.count else 0.0,
        "status_codes": {str(k): v for k, v in sorted(codes.items())},
        "first_error": first_error,
        "latency": _stats(ms),
        "throughput_points_per_sec": round(args.count / (sum(ms) / 1000), 2) if sum(ms) else 0.0,
        "cleanup": f"python -m scripts.delete_patient --user-id {args.patient} --confirm",
    }

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUT_DIR / "gps_ingest_summary.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    if landed != accepted:
        print(f"\nWARNING: {accepted} requests were accepted but {landed} rows landed. "
              f"An accepted request whose row never arrived is the fault this "
              f"measurement exists to catch.")


if __name__ == "__main__":
    asyncio.run(main())
