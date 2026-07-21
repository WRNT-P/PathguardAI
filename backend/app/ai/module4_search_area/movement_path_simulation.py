# pathguard/backend/app/ai/module4_search_area/movement_path_simulation.py
"""Module 4.2 — Movement Path Simulation.

Combines two algorithms as specified:

1. Monte Carlo Simulation — generates N random path endpoints within the
   search radius, biased toward (a) the patient's last known direction and
   (b) familiar places weighted by visit frequency. No external routing API
   is required; the simulation uses vectorised numpy math on spherical coords.

2. A* Pathfinding (simplified offline version) — for the top-5 familiar
   destinations reachable within the search radius, computes a straight-line
   waypoint sequence along the bearing from the last known position. A full
   road-network A* would require an external OSM API; this version gives
   directional waypoints that the frontend can render as path overlays and
   that the KDE step can sample from.

No DB, no side effects. Accepts both ORM objects and dicts everywhere.
"""
from __future__ import annotations

import math
import random

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False

from ._geo import EARTH_RADIUS_KM, haversine_m as _haversine_m

_DEG_PER_M_LAT = 1.0 / 111_320.0  # metres → degrees latitude (constant)


def _deg_per_m_lng(lat_deg: float) -> float:
    return 1.0 / (111_320.0 * math.cos(math.radians(lat_deg)))


def _bearing_rad(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
    dlng = math.radians(to_lng - from_lng)
    lat1 = math.radians(from_lat)
    lat2 = math.radians(to_lat)
    x = math.sin(dlng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return math.atan2(x, y)


def _destination_point(lat: float, lng: float, bearing_rad: float, distance_m: float) -> tuple[float, float]:
    """Compute the destination point given start, bearing, and distance."""
    R = EARTH_RADIUS_KM * 1000.0
    d = distance_m / R
    lat1 = math.radians(lat)
    lng1 = math.radians(lng)
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(bearing_rad))
    lng2 = lng1 + math.atan2(
        math.sin(bearing_rad) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lng2)


class PathSimulator:
    """Simulate likely paths a missing patient may have taken.

    Usage:
        sim = PathSimulator(n_simulations=10_000)
        sim.fit(gps_history)
        result = sim.simulate_paths(last_lat, last_lng, last_direction_deg,
                                    search_radius_m, known_places)
    """

    def __init__(self, n_simulations: int = 10_000):
        self.n_simulations = n_simulations
        self._fitted = False
        self._historical_bearings: list[float] = []  # radians

    # ── fit ──────────────────────────────────────────────────────────────────

    def fit(self, gps_records: list) -> None:
        """Learn directional tendencies from GPS history.

        Extracts all consecutive-point bearings from the historical track so
        the Monte Carlo simulation can sample from observed directions rather
        than pure uniform random. Familiar places are applied later, in
        ``simulate_paths`` (A* routes), so they are not needed here.
        """
        bearings: list[float] = []
        pts = []
        for r in gps_records:
            if isinstance(r, dict):
                lat = r.get("smooth_latitude") or r.get("latitude")
                lng = r.get("smooth_longitude") or r.get("longitude")
            else:
                lat = getattr(r, "smooth_latitude", None) or getattr(r, "latitude", None)
                lng = getattr(r, "smooth_longitude", None) or getattr(r, "longitude", None)
            if lat is not None and lng is not None:
                pts.append((float(lat), float(lng)))

        for i in range(1, len(pts)):
            b = _bearing_rad(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
            bearings.append(b)

        self._historical_bearings = bearings
        self._fitted = True

    # ── simulate_paths ───────────────────────────────────────────────────────

    def simulate_paths(
        self,
        last_lat: float,
        last_lng: float,
        last_direction_deg: float | None,
        search_radius_m: float,
        known_places: list[dict],
    ) -> dict:
        """Generate simulated path endpoints and A* familiar-place routes.

        Monte Carlo endpoints are sampled as follows:
          70% of paths: biased toward last_direction_deg ± 45° (or sampled
              from historical bearings if fit() was called and produced data).
          30% of paths: uniform random bearing (exploratory).

        Each endpoint's radial distance is drawn as r = radius·√U (U uniform in
        [0, 1]), which spreads endpoints uniformly across the disc area rather
        than piling them near the origin.

        Returns:
            {
                "status": "ok",
                "endpoints": [[lat, lng], ...],     # all N simulated endpoints
                "familiar_paths": [                  # A* waypoint sequences
                    {
                        "place_name": str,
                        "visit_frequency": int,
                        "distance_m": float,
                        "waypoints": [[lat, lng], ...],
                    },
                    ...
                ],
                "_meta": {...},
            }
        """
        if search_radius_m <= 0:
            return {"status": "no_radius", "endpoints": [], "familiar_paths": [], "_meta": {}}

        # ── Monte Carlo ───────────────────────────────────────────────────────
        endpoints = self._monte_carlo(last_lat, last_lng, last_direction_deg, search_radius_m)

        # ── A* familiar-place routes ──────────────────────────────────────────
        # คำนวณ เส้นทางที่สมจริง (ตามถนน/ทางเดินจริง ไม่ใช่เส้นตรงทะลุตึก) จากจุดเริ่มต้นไปยังสถานที่คุ้นเคยแต่ละแห่ง
        #ความต่างจาก Monte Carlo: Monte Carlo คือการ "สุ่มเดา" เส้นทางแบบกว้างๆ ทั่วไป ส่วน A* ตรงนี้คือการ "วางแผนเส้นทางเฉพาะเจาะจง" ไปยังสถานที่ที่รู้อยู่แล้วว่าผู้ป่วยชอบไป 
        familiar_paths = self._astar_familiar(last_lat, last_lng, search_radius_m, known_places)

        # Add familiar-path endpoints to the simulation pool so KDE includes them
        # บรรทัดที่ 3-5: เอาปลายทางจาก A* ไปรวมกับ Monte Carlo
        for fp in familiar_paths:
            if fp["waypoints"]:
                endpoints.append(fp["waypoints"][-1])

        return {
            "status": "ok",
            "endpoints": endpoints,
            "familiar_paths": familiar_paths,
            "_meta": {
                "n_simulations": self.n_simulations,
                "fitted": self._fitted,
                "historical_bearings_count": len(self._historical_bearings),
                "familiar_paths_count": len(familiar_paths),
                "search_radius_m": search_radius_m,
            },
        }

    # ── internals ─────────────────────────────────────────────────────────────

    def _monte_carlo(
        self,
        origin_lat: float,
        origin_lng: float,
        last_direction_deg: float | None,
        radius_m: float,
    ) -> list[list[float]]:
        # Unseeded RNG: each request draws an independent sample. A fixed seed
        # would make every request (and every patient with the same radius)
        # replay the identical cloud, defeating the point of Monte Carlo.
        rng = random.Random()

        biased_count = int(self.n_simulations * 0.70)
        random_count = self.n_simulations - biased_count

        last_bearing_rad = (
            math.radians(last_direction_deg) if last_direction_deg is not None else None
        )

        endpoints: list[list[float]] = []

        # Biased samples
        for _ in range(biased_count):
            # Choose bearing: historical bias if available, else last direction
            """
            ในลูปนี้ เลือก "ทิศทางตั้งต้น" (base bearing) โดยมีลำดับความสำคัญ 3 ระดับ:
                ถ้ามีข้อมูลประวัติทิศทางการเดิน (self._historical_bearings — น่าจะมาจากตอน sim.fit(gps_history) ที่เรียนรู้จาก 30 วันที่ผ่านมา) → สุ่มเลือก 1 ทิศจากประวัติจริง ของผู้ป่วยคนนี้
                ถ้าไม่มีประวัติ แต่มีทิศทางล่าสุดตอนหาย → ใช้ทิศทางนั้นเป็นฐาน
                ถ้าไม่มีข้อมูลอะไรเลย → สุ่มทิศทางแบบเปิดกว้าง (-π ถึง π คือ -180° ถึง 180°)
            """
            if self._historical_bearings:
                base_b = rng.choice(self._historical_bearings)
            elif last_bearing_rad is not None:
                base_b = last_bearing_rad
            else:
                base_b = rng.uniform(-math.pi, math.pi)
            # Perturb ±45°
            bearing = base_b + rng.uniform(-math.pi / 4, math.pi / 4)
            dist = self._sample_distance(rng, radius_m)
            lat, lng = _destination_point(origin_lat, origin_lng, bearing, dist)
            endpoints.append([lat, lng])

        # Random exploratory samples
        # 30% ที่เหลือสุ่มทิศทาง แบบเปิดกว้างเต็ม 360° ทุกจุด แล้วคำนวณจุดปลายทางเหมือนกัน
        for _ in range(random_count):
            bearing = rng.uniform(-math.pi, math.pi)
            dist = self._sample_distance(rng, radius_m)
            lat, lng = _destination_point(origin_lat, origin_lng, bearing, dist)
            endpoints.append([lat, lng])

        return endpoints

    @staticmethod
    def _sample_distance(rng: random.Random, radius_m: float) -> float:
        """Sample a radial distance uniformly over the disc *area*.

        In 2D polar coordinates the area element is r·dr·dθ, so drawing r from
        Uniform(0, R) piles points near the origin (density ∝ 1/r). Using
        r = R·√U spreads endpoints uniformly across the disc, so the KDE
        reflects the simulated bearings instead of an artificial centre spike.
        """
        return radius_m * math.sqrt(rng.random())

    def _astar_familiar(
        self,
        origin_lat: float,
        origin_lng: float,
        radius_m: float,
        known_places: list[dict],
    ) -> list[dict]:
        """Straight-line waypoint sequences toward the top-5 reachable familiar places."""
        reachable = []
        for p in known_places:
            plat = p.get("latitude")
            plng = p.get("longitude")
            if plat is None or plng is None:
                continue
            dist = _haversine_m(origin_lat, origin_lng, float(plat), float(plng))
            if dist <= radius_m:
                reachable.append({
                    "place_name": p.get("place_name", p.get("name", "unknown")),
                    "visit_frequency": p.get("visit_frequency", 0),
                    "lat": float(plat),
                    "lng": float(plng),
                    "distance_m": round(dist, 1),
                })

        # Sort by visit frequency descending, take top 5
        reachable.sort(key=lambda x: x["visit_frequency"], reverse=True)
        top5 = reachable[:5]

        paths = []
        for place in top5:
            waypoints = self._waypoints(origin_lat, origin_lng, place["lat"], place["lng"])
            paths.append({
                "place_name": place["place_name"],
                "visit_frequency": place["visit_frequency"],
                "distance_m": place["distance_m"],
                "waypoints": waypoints,
            })
        return paths

    @staticmethod
    def _waypoints(
        from_lat: float, from_lng: float,
        to_lat: float, to_lng: float,
        n_steps: int = 10,
    ) -> list[list[float]]:
        """Interpolate n_steps waypoints along the great-circle arc."""
        bearing = _bearing_rad(from_lat, from_lng, to_lat, to_lng)
        total_dist = _haversine_m(from_lat, from_lng, to_lat, to_lng)
        waypoints = []
        for i in range(1, n_steps + 1):
            d = total_dist * i / n_steps
            lat, lng = _destination_point(from_lat, from_lng, bearing, d)
            waypoints.append([round(lat, 6), round(lng, 6)])
        return waypoints


if __name__ == "__main__":
    places = [
        {"latitude": 13.757, "longitude": 100.502, "visit_frequency": 10, "place_name": "Park"},
        {"latitude": 13.758, "longitude": 100.503, "visit_frequency": 5, "place_name": "Shop"},
        {"latitude": 13.900, "longitude": 100.700, "visit_frequency": 3, "place_name": "Far"},
    ]
    origin = (13.7563, 100.5018)

    sim = PathSimulator(n_simulations=1000)

    # Without fit (direction-only bias)
    result = sim.simulate_paths(*origin, 90.0, 1200.0, places)
    assert result["status"] == "ok", result
    assert len(result["endpoints"]) >= 1000, len(result["endpoints"])
    assert len(result["familiar_paths"]) == 2, result["familiar_paths"]  # only 2 within 1200m
    assert result["familiar_paths"][0]["place_name"] == "Park"  # highest frequency first

    # Fit and re-simulate
    gps_records = [
        {"latitude": 13.756 + i * 0.001, "longitude": 100.501 + i * 0.001}
        for i in range(20)
    ]
    sim.fit(gps_records)
    assert sim._fitted is True
    assert len(sim._historical_bearings) == 19

    result2 = sim.simulate_paths(*origin, None, 1200.0, places)
    assert result2["status"] == "ok"
    assert result2["_meta"]["historical_bearings_count"] == 19

    # Zero radius → no_radius
    result3 = sim.simulate_paths(*origin, 90.0, 0.0, places)
    assert result3["status"] == "no_radius"

    print("movement_path_simulation: all assertions passed")
