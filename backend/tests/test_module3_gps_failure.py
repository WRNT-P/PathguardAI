"""Module 3.3 — GPS failure handling (gap detection, safety-biased)."""
from datetime import datetime, timedelta

from app.ai.module3_risk.gps_failure_handling import detect_gps_gap

NOW = datetime(2026, 6, 26, 12, 0, 0)


def reading(seconds_ago, *, as_iso=False, ts_key="recorded_at"):
    t = NOW - timedelta(seconds=seconds_ago)
    return {
        "latitude": 13.7563,
        "longitude": 100.5018,
        ts_key: t.isoformat() if as_iso else t,
    }


def test_gap_under_threshold_not_lost():
    r = detect_gps_gap(reading(300), NOW)
    assert r["gps_lost"] is False
    assert r["gap_seconds"] == 300.0


def test_gap_over_threshold_lost():
    r = detect_gps_gap(reading(845), NOW)
    assert r["gps_lost"] is True
    assert r["gap_seconds"] == 845.0
    assert r["last_known"]["latitude"] == 13.7563


def test_exactly_at_threshold_not_lost():
    # strict > threshold
    r = detect_gps_gap(reading(600), NOW)
    assert r["gps_lost"] is False
    assert r["gap_seconds"] == 600.0


def test_no_reading_is_lost():
    assert detect_gps_gap(None, NOW) == {
        "gps_lost": True,
        "gap_seconds": None,
        "last_known": None,
    }


def test_iso_and_datetime_timestamps_agree():
    assert detect_gps_gap(reading(845, as_iso=True), NOW) == detect_gps_gap(
        reading(845, as_iso=False), NOW
    )


def test_z_suffix_iso_parses_against_tz_aware_now():
    now_utc = datetime.fromisoformat("2026-06-26T12:00:00+00:00")
    r = detect_gps_gap(
        {"latitude": 1.0, "longitude": 2.0, "recorded_at": "2026-06-26T11:45:55Z"},
        now_utc,
    )
    assert r["gps_lost"] is True
    assert r["gap_seconds"] == 845.0


def test_alternate_timestamp_key():
    r = detect_gps_gap(reading(845, ts_key="timestamp"), NOW)
    assert r["gps_lost"] is True


def test_unparseable_timestamp_is_lost_but_keeps_position():
    r = detect_gps_gap(
        {"latitude": 1.0, "longitude": 2.0, "recorded_at": "not-a-date"}, NOW
    )
    assert r["gps_lost"] is True
    assert r["gap_seconds"] is None
    assert r["last_known"] == {"latitude": 1.0, "longitude": 2.0, "recorded_at": None}


def test_tz_aware_vs_naive_mismatch_is_lost():
    aware = {
        "latitude": 1.0,
        "longitude": 2.0,
        "recorded_at": "2026-06-26T11:00:00+00:00",
    }
    r = detect_gps_gap(aware, NOW)  # NOW is naive -> can't compare -> lost
    assert r["gps_lost"] is True
    assert r["gap_seconds"] is None
