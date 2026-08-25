"""Bridge: GPS lat/lng → nearest cluster_id from behavioral profile.
Converts raw coordinates to cluster_ids Module 2's LSTM understands.
"""
import math
from typing import Optional

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_nearest_cluster(lat: float, lng: float, known_places: list[dict],
                         max_distance_km: float = 0.15) -> Optional[int]:
    """Return cluster_id of the nearest known place the point falls inside, or None.

    A place may carry its own ``radius_m``: a house is the 150 m default, but a
    hospital or market compound is bigger, and matching those against 150 m reads
    every walk across the grounds as leaving a familiar place. Places without a
    radius fall back to ``max_distance_km``, so a profile written before radii
    existed behaves exactly as it did.

    Each place is tested against its OWN radius before the nearest one wins —
    thresholding the single nearest place would let a tight pin next to a wide one
    swallow the match and return None.
    """
    best_id: Optional[int] = None
    best_dist = float('inf')
    for place in known_places:
        dist = haversine_km(lat, lng, place['latitude'], place['longitude'])
        radius_m = place.get('radius_m')
        radius_km = radius_m / 1000.0 if radius_m else max_distance_km
        if dist <= radius_km and dist < best_dist:
            best_dist = dist
            best_id = place['cluster_id']
    return best_id


def get_familiarity(known_places: list[dict], cluster_id: int) -> float:
    """Normalized visit frequency as a familiarity proxy (0..1)."""
    freqs = [p.get('visit_frequency', 0) for p in known_places]
    max_freq = max(freqs) if freqs else 1
    for p in known_places:
        if p['cluster_id'] == cluster_id:
            return min(p.get('visit_frequency', 0) / max_freq, 1.0)
    return 0.0


def bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Bearing from point 1 -> point 2, in degrees (0-360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    x = math.sin(dl) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angle_diff(a: float, b: float) -> float:
    """Circular difference between two bearings, in degrees (0-180)."""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def get_lat_lng(record) -> tuple[float, float]:
    """Read latitude/longitude from either a dict or an ORM-style object."""
    if isinstance(record, dict):
        return float(record["latitude"]), float(record["longitude"])
    return float(record.latitude), float(record.longitude)


def get_speed(record) -> float | None:
    """Read speed from either a dict or an ORM-style object."""
    if isinstance(record, dict):
        return record.get("speed")
    return getattr(record, "speed", None)
