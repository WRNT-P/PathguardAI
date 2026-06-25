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
    """Return cluster_id of the nearest known place, or None (unknown)."""
    best_id: Optional[int] = None
    best_dist = float('inf')
    for place in known_places:
        dist = haversine_km(lat, lng, place['latitude'], place['longitude'])
        if dist < best_dist:
            best_dist = dist
            best_id = place['cluster_id']
    if best_dist <= max_distance_km:
        return best_id
    return None


def get_familiarity(known_places: list[dict], cluster_id: int) -> float:
    """Normalized visit frequency as a familiarity proxy (0..1)."""
    freqs = [p.get('visit_frequency', 0) for p in known_places]
    max_freq = max(freqs) if freqs else 1
    for p in known_places:
        if p['cluster_id'] == cluster_id:
            return min(p.get('visit_frequency', 0) / max_freq, 1.0)
    return 0.0
