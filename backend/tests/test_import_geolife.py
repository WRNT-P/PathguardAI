"""Unit tests for the GeoLife import geometry (issue #1 Phase 2).

Pure math — no DB. Checks the derived speed/direction building blocks against
hand-computable answers so a regression in the formulas is caught early.
"""
import math

from scripts.import_geolife import bearing_deg, haversine_m


def test_haversine_zero_distance():
    assert haversine_m(13.7, 100.5, 13.7, 100.5) == 0.0


def test_haversine_one_degree_longitude_at_equator():
    # 1° of longitude at the equator ≈ 111.19 km for r=6371 km.
    d = haversine_m(0.0, 0.0, 0.0, 1.0)
    assert math.isclose(d, 111_194.9, rel_tol=1e-3)


def test_bearing_due_north():
    assert math.isclose(bearing_deg(0.0, 0.0, 1.0, 0.0), 0.0, abs_tol=1e-6)


def test_bearing_due_east():
    assert math.isclose(bearing_deg(0.0, 0.0, 0.0, 1.0), 90.0, abs_tol=1e-6)


def test_bearing_is_in_range():
    b = bearing_deg(13.7, 100.5, 13.8, 100.4)  # north-west-ish
    assert 0.0 <= b < 360.0
    assert 270.0 < b < 360.0  # heading roughly NW
