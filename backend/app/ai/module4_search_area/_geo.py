# pathguard/backend/app/ai/module4_search_area/_geo.py
"""Shared geo helpers for Module 4 — avoids re-deriving haversine per file."""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)) * 1000.0
