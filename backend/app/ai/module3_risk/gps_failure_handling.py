# pathguard/backend/app/ai/module3_risk/gps_failure_handling.py
"""Module 3.3 — GPS Failure Handling.

Pure detector: decides whether a patient's GPS feed has gone silent for too
long, and packages the last known fix for downstream search. No DB, no Module 2
or Module 4 imports, no side effects.

It does NOT call Module 4 (still empty) — the returned dict *is* the handoff
contract Module 4 will consume: last_known lat/lng + time is what its
search-radius math needs (Distance = Speed × Time).

Safety-biased on the unknown: anything we can't evaluate is treated as
GPS-lost (``gps_lost=True``) rather than assumed fine, consistent with Module
3's "unknown ⇒ more caution" principle. That covers a missing reading, a
missing/unparseable timestamp, and a timestamp that can't be compared to
``now`` (e.g. tz-aware vs naive).

Timestamp parsing mirrors Module 2's wandering_detection (accept ``recorded_at``
or ``timestamp``; parse ISO strings with a ``Z`` -> ``+00:00`` fallback).
"""
from __future__ import annotations

from datetime import datetime


def _get_lat_lng(r) -> tuple[float, float]:
    """Read latitude/longitude from a dict or ORM-style object."""
    if isinstance(r, dict):
        return float(r["latitude"]), float(r["longitude"])
    return float(r.latitude), float(r.longitude)


def _get_timestamp(r):
    """Read the reading's time, preferring ``recorded_at`` then ``timestamp``."""
    if isinstance(r, dict):
        return r.get("recorded_at") or r.get("timestamp")
    return getattr(r, "recorded_at", getattr(r, "timestamp", None))


def _parse_timestamp(ts):
    """Coerce a datetime or ISO string to datetime; None if unparseable."""
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def detect_gps_gap(last_reading, now: datetime, threshold_s: float = 600.0) -> dict:
    """Detect a GPS gap from the last reading; return the Module-4 handoff payload.

    ``gps_lost`` is True when the silence exceeds ``threshold_s`` (strict ``>``,
    so exactly at the threshold is still fine). Unknowns are biased to lost.
    """
    # No reading at all is itself a lost-GPS condition.
    if not last_reading:
        return {"gps_lost": True, "gap_seconds": None, "last_known": None}

    lat, lng = _get_lat_lng(last_reading)
    last_time = _parse_timestamp(_get_timestamp(last_reading))

    # Missing/unparseable time: keep the position, but we can't measure the gap.
    if last_time is None:
        return {
            "gps_lost": True,
            "gap_seconds": None,
            "last_known": {"latitude": lat, "longitude": lng, "recorded_at": None},
        }

    last_known = {
        "latitude": lat,
        "longitude": lng,
        "recorded_at": last_time.isoformat(),
    }

    try:
        raw_gap = (now - last_time).total_seconds()
    except TypeError:
        # tz-aware vs naive mismatch — can't compare, so treat as lost.
        return {"gps_lost": True, "gap_seconds": None, "last_known": last_known}

    return {
        "gps_lost": raw_gap > threshold_s,
        "gap_seconds": round(raw_gap, 1),
        "last_known": last_known,
    }


if __name__ == "__main__":
    from datetime import timedelta

    NOW = datetime(2026, 6, 26, 12, 0, 0)

    def reading(seconds_ago: float, *, as_iso: bool = False, ts_key: str = "recorded_at"):
        t = NOW - timedelta(seconds=seconds_ago)
        return {"latitude": 13.7563, "longitude": 100.5018, ts_key: t.isoformat() if as_iso else t}

    # gap under threshold -> not lost
    r = detect_gps_gap(reading(300), NOW)
    assert r["gps_lost"] is False, r
    assert r["gap_seconds"] == 300.0, r

    # gap over threshold -> lost (the 845s example)
    r = detect_gps_gap(reading(845), NOW)
    assert r["gps_lost"] is True, r
    assert r["gap_seconds"] == 845.0, r
    assert r["last_known"]["latitude"] == 13.7563, r

    # exactly at threshold (600s) -> not lost (strict >)
    r = detect_gps_gap(reading(600), NOW)
    assert r["gps_lost"] is False, r
    assert r["gap_seconds"] == 600.0, r

    # no reading -> lost, last_known None
    r = detect_gps_gap(None, NOW)
    assert r == {"gps_lost": True, "gap_seconds": None, "last_known": None}, r

    # ISO string vs datetime timestamps parse to the same result
    r_iso = detect_gps_gap(reading(845, as_iso=True), NOW)
    r_dt = detect_gps_gap(reading(845, as_iso=False), NOW)
    assert r_iso == r_dt, (r_iso, r_dt)
    assert r_iso["gps_lost"] is True and r_iso["gap_seconds"] == 845.0, r_iso

    # "Z"-suffixed ISO parses via the +00:00 fallback (compared against a tz-aware now)
    now_utc = datetime.fromisoformat("2026-06-26T12:00:00+00:00")
    r = detect_gps_gap(
        {"latitude": 1.0, "longitude": 2.0, "recorded_at": "2026-06-26T11:45:55Z"},
        now_utc,
    )
    assert r["gps_lost"] is True and r["gap_seconds"] == 845.0, r

    # alternate "timestamp" key is honored
    r = detect_gps_gap(reading(845, ts_key="timestamp"), NOW)
    assert r["gps_lost"] is True, r

    # missing/unparseable timestamp -> lost, position kept, recorded_at None
    r = detect_gps_gap({"latitude": 1.0, "longitude": 2.0, "recorded_at": "not-a-date"}, NOW)
    assert r["gps_lost"] is True and r["gap_seconds"] is None, r
    assert r["last_known"] == {"latitude": 1.0, "longitude": 2.0, "recorded_at": None}, r

    print("gps_failure_handling: all assertions passed")
