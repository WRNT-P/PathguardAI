# pathguard/backend/app/ai/module4_search_area/last_known_position.py
"""Module 4.1 — Last Known Position Analysis.

Pure functions: no DB, no side effects. Receives either ORM objects or dicts
(same dual-input pattern as gps_failure_handling). Calculates the maximum
search radius from speed × time, and extracts a clean last-known location dict
from a raw GPS record.

Safety-biased defaults:
  - Missing speed  → 1.4 m/s (brisk walk; overestimates → larger radius → safer)
  - Missing direction → None (direction-agnostic simulation)
  - Smooth lat/lng preferred over raw when available
"""
from __future__ import annotations

from datetime import datetime

_DEFAULT_SPEED_MS = 1.4  # m/s — cautious overestimate when speed unknown


def _get_attr(record, *keys):
    """Return the first non-None value found across the given keys."""
    for key in keys:
        if isinstance(record, dict):
            v = record.get(key)
        else:
            v = getattr(record, key, None)
        if v is not None:
            return v
    return None


def _parse_ts(ts):
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def calculate_search_radius(last_speed_ms: float | None, time_missing_minutes: float) -> float:
    """Return maximum search radius in metres using Distance = Speed × Time.

    A None or non-positive speed falls back to the cautious default (1.4 m/s)
    so the search area is never under-estimated.
    """
    if last_speed_ms is None or last_speed_ms <= 0:
        speed = _DEFAULT_SPEED_MS
    else:
        speed = last_speed_ms
    return round(speed * time_missing_minutes * 60.0, 1)


def extract_last_known(last_gps_record) -> dict:
    """Extract a clean last-known dict from an ORM row or GPS dict.

    Prefers Kalman-smoothed coordinates (smooth_latitude / smooth_longitude)
    when present; falls back to raw latitude / longitude.

    Returns:
        {
            "lat": float,
            "lng": float,
            "speed_ms": float | None,
            "direction_deg": float | None,
            "recorded_at": str | None,   # ISO-8601
            "_meta": {...},
        }
    """
    if last_gps_record is None:
        return {
            "lat": None,
            "lng": None,
            "speed_ms": _DEFAULT_SPEED_MS,
            "direction_deg": None,
            "recorded_at": None,
            "_meta": {"source": "none", "speed_defaulted": True},
        }

    lat = _get_attr(last_gps_record, "smooth_latitude", "latitude")
    lng = _get_attr(last_gps_record, "smooth_longitude", "longitude")

    # A record can exist yet carry no usable coordinates (partial insert before
    # a satellite fix, or a schema gap). Bail out with None coords instead of
    # crashing on float(None) — the caller's None-guard then handles it.
    if lat is None or lng is None:
        return {
            "lat": None,
            "lng": None,
            "speed_ms": _DEFAULT_SPEED_MS,
            "direction_deg": None,
            "recorded_at": None,
            "_meta": {"source": "gps_record", "missing_coords": True},
        }

    speed = _get_attr(last_gps_record, "speed")
    direction = _get_attr(last_gps_record, "direction")
    ts_raw = _get_attr(last_gps_record, "recorded_at", "timestamp")
    ts = _parse_ts(ts_raw)

    used_default_speed = speed is None or speed <= 0
    if used_default_speed:
        speed = _DEFAULT_SPEED_MS

    smooth_used = _get_attr(last_gps_record, "smooth_latitude") is not None

    return {
        "lat": float(lat),
        "lng": float(lng),
        "speed_ms": float(speed),
        "direction_deg": float(direction) if direction is not None else None,
        "recorded_at": ts.isoformat() if ts else None,
        "_meta": {
            "source": "gps_record",
            "smooth_coords": smooth_used,
            "speed_defaulted": used_default_speed,
        },
    }


if __name__ == "__main__": # test function
    # ── calculate_search_radius ──────────────────────────────────────────────
    assert calculate_search_radius(0.8, 25) == 1200.0, "0.8 m/s × 25 min = 1200 m"
    assert calculate_search_radius(None, 10) == _DEFAULT_SPEED_MS * 10 * 60, "None speed → default"
    assert calculate_search_radius(0.0, 10) == _DEFAULT_SPEED_MS * 10 * 60, "Zero speed → default"
    assert calculate_search_radius(1.0, 0) == 0.0, "Zero time → 0 m"

    # ── extract_last_known: dict input ───────────────────────────────────────
    rec = {
        "latitude": 13.7563, "longitude": 100.5018,
        "smooth_latitude": 13.7564, "smooth_longitude": 100.5019,
        "speed": 0.9, "direction": 90.0,
        "recorded_at": "2026-06-26T12:00:00",
    }
    out = extract_last_known(rec)
    assert out["lat"] == 13.7564, "smooth_latitude preferred"
    assert out["speed_ms"] == 0.9
    assert out["direction_deg"] == 90.0
    assert out["_meta"]["smooth_coords"] is True

    # dict without smooth fields
    rec2 = {"latitude": 1.0, "longitude": 2.0}
    out2 = extract_last_known(rec2)
    assert out2["lat"] == 1.0
    assert out2["speed_ms"] == _DEFAULT_SPEED_MS, "missing speed → default"
    assert out2["_meta"]["speed_defaulted"] is True

    # None record
    out3 = extract_last_known(None)
    assert out3["lat"] is None
    assert out3["speed_ms"] == _DEFAULT_SPEED_MS

    # Record exists but has no coordinates → None coords, no crash (Bug 2)
    out4 = extract_last_known({"speed": 0.5, "recorded_at": "2026-06-26T12:00:00"})
    assert out4["lat"] is None and out4["lng"] is None
    assert out4["_meta"]["missing_coords"] is True

    print("last_known_position: all assertions passed")
