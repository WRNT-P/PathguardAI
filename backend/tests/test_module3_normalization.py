"""Module 3.1 — data normalization (pure math, no DB/ML)."""
import math

import pytest

from app.ai.module3_risk.data_normalization import (
    compute_unfamiliarity,
    convert_boolean,
    normalize_route_deviation,
    scale_wandering,
)


@pytest.mark.parametrize(
    "meters, expected",
    [
        (0.0, 0.0),
        (250.0, 0.5),
        (320.0, 0.64),     # spec example
        (500.0, 1.0),      # ceiling
        (600.0, 1.0),      # clamps above ceiling
        (-5.0, 0.0),       # clamps below zero
    ],
)
def test_normalize_route_deviation(meters, expected):
    assert math.isclose(normalize_route_deviation(meters), expected, abs_tol=1e-9)


def test_normalize_route_deviation_custom_ceiling():
    assert normalize_route_deviation(100.0, max_distance=200.0) == 0.5


@pytest.mark.parametrize(
    "score, expected",
    [(0.0, 0.0), (0.7, 0.7), (1.0, 1.0), (1.4, 1.0), (-0.3, 0.0)],
)
def test_scale_wandering_clamps(score, expected):
    assert scale_wandering(score) == expected


def test_convert_boolean():
    assert convert_boolean(True) == 1.0
    assert convert_boolean(False) == 0.0


@pytest.mark.parametrize(
    "familiarity, expected",
    [(0.0, 1.0), (1.0, 0.0), (0.30, 0.70), (-0.2, 1.0), (1.3, 0.0)],
)
def test_compute_unfamiliarity(familiarity, expected):
    assert math.isclose(compute_unfamiliarity(familiarity), expected, abs_tol=1e-9)
