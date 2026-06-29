# pathguard/backend/app/models/search_area.py
"""Pydantic response model for Module 4 — Search Area Prediction."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ProbabilityZone(BaseModel):
    latitude: float
    longitude: float
    probability: float


class FamiliarPath(BaseModel):
    place_name: str
    visit_frequency: int
    distance_m: float
    waypoints: list[list[float]]  # [[lat, lng], ...]


class TargetLocation(BaseModel):
    name: str
    latitude: float
    longitude: float
    visit_frequency: int
    distance_m: float


class GridBounds(BaseModel):
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float


class SearchAreaResponse(BaseModel):
    patient_id: int
    status: Literal["ok", "no_data", "gps_active"]
    message: str
    last_known_location: dict | None = None
    search_radius_meters: float | None = None
    adjusted_radius_meters: float | None = None
    adjustment_reason: str | None = None
    high_probability_zone: list[ProbabilityZone] | None = None
    medium_probability_zone: list[ProbabilityZone] | None = None
    low_probability_zone: list[ProbabilityZone] | None = None
    target_locations: list[TargetLocation] | None = None
    familiar_paths: list[FamiliarPath] | None = None
    grid_bounds: GridBounds | None = None
