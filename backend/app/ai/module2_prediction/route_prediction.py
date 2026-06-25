"""Module 2.2 — Route Prediction using Hidden Markov Model (HMM).

Predicts the route a patient will take from their current GPS position
to a predicted destination (from Module 2.1 DestinationPredictor).

Design: Sync / pipeline style — no DB calls inside this class.
        Caller fetches gps_records and known_places, then passes them in.

──────────────────────────────────────────────────────────────────────────
ทฤษฎี HMM ที่ใช้:
  Hidden States    = cluster_id  (สถานที่ที่ผู้ป่วยผ่านระหว่างเดินทาง)
  Observations     = GPS (lat, lng) จริงที่วัดได้
  Transition A     = P(ไป cluster B ต่อจาก cluster A) — เรียนรู้จาก history
  Emission B       = Gaussian บน haversine distance ถึง centroid ของ cluster
  Decoding         = Viterbi หา cluster sequence ที่น่าจะเป็นที่สุด
  Similarity Score = DTW ระหว่างเส้นทางที่ทำนาย vs เส้นทางเก่าสู่ destination เดิม
──────────────────────────────────────────────────────────────────────────

Usage (pipeline หลัง DestinationPredictor.predict()):
------------------------------------------------------
    gps_records  = await crud.get_gps_history(db, patient_id, days=30)
    known_places = json.loads(profile.known_places)

    predictor = RoutePredictor()
    predictor.fit(gps_records, known_places)

    recent_gps = gps_records[-20:]        # 20 จุดล่าสุด
    result = predictor.predict_route(
        recent_gps=recent_gps,
        destination_cluster_id=dest_cluster_id,   # จาก DestinationPredictor
        known_places=known_places,
    )
    # result["predicted_route"]   → list of waypoints
    # result["similarity_score"]  → 0.0–1.0
    # result["route_familiar"]    → True ถ้า score >= 0.6
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from app.ai.module2_prediction.cluster_matcher import haversine_km


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_lat_lng(record) -> tuple[float, float]:
    """รองรับทั้ง GPSData ORM object และ plain dict."""
    if isinstance(record, dict):
        return float(record["latitude"]), float(record["longitude"])
    return float(record.latitude), float(record.longitude)


def _get_timestamp(record):
    """คืน recorded_at จาก ORM object หรือ dict — อาจเป็น None."""
    if isinstance(record, dict):
        return record.get("recorded_at") or record.get("timestamp")
    return getattr(record, "recorded_at", None)


def _nearest_cluster(lat: float, lng: float, known_places: list[dict],
                     max_dist_km: float = 0.15) -> int | None:
    """คืน cluster_id ที่ใกล้ที่สุดภายใน max_dist_km หรือ None ถ้าไม่มี."""
    best_id, best_dist = None, float("inf")
    for p in known_places:
        d = haversine_km(lat, lng, p["latitude"], p["longitude"])
        if d < best_dist:
            best_dist = d
            best_id = p["cluster_id"]
    return best_id if best_dist <= max_dist_km else None


# ─────────────────────────────────────────────────────────────────────────────
# RoutePredictor
# ─────────────────────────────────────────────────────────────────────────────

class RoutePredictor:
    """
    ทำนายเส้นทางของผู้ป่วยจากตำแหน่งปัจจุบัน → สถานที่ปลายทาง (จาก Module 2.1)

    Parameters
    ----------
    emission_sigma_m : ค่า σ ของ Gaussian emission (เมตร)
                       100 m เหมาะกับ DBSCAN eps=50 m cluster ที่ใช้ใน Module 1
    max_hops         : จำนวน intermediate cluster สูงสุดในเส้นทาง
    """

    def __init__(self, emission_sigma_m: float = 100.0, max_hops: int = 10):
        self.emission_sigma_m = emission_sigma_m
        self.max_hops = max_hops

        # เรียนรู้จาก fit()
        self.transition_matrix: Optional[np.ndarray] = None  # shape (n_clusters, n_clusters)
        self.initial_probs: Optional[np.ndarray] = None       # shape (n_clusters,)
        self.n_clusters: int = 0
        self._historical_routes: list[list[int]] = []         # cluster_id sequences ต่อวัน

    # ── fit ───────────────────────────────────────────────────────────────────

    def fit(self, gps_records: list, known_places: list[dict]) -> dict:
        """
        เรียนรู้ HMM parameters จาก GPS history ย้อนหลัง

        Parameters
        ----------
        gps_records  : list of GPSData (ORM) หรือ dict ที่มี latitude/longitude/recorded_at
        known_places : cluster list จาก BehavioralProfile.known_places

        Returns
        -------
        dict สรุปผล fit
        """
        if not known_places:
            return {"status": "skipped", "reason": "no known_places"}
        if not gps_records:
            return {"status": "skipped", "reason": "no gps_records"}

        n = max(p["cluster_id"] for p in known_places) + 1
        self.n_clusters = n

        # ── Map GPS records → cluster_id sequence ─────────────────────────────
        cluster_seq: list[int | None] = []
        day_seq: list[str] = []

        for r in gps_records:
            lat, lng = _get_lat_lng(r)
            cid = _nearest_cluster(lat, lng, known_places)
            cluster_seq.append(cid)
            ts = _get_timestamp(r)
            day_seq.append(ts.strftime("%Y-%m-%d") if ts else "unknown")

        # ── Transition count matrix ──────────────────────────────────────────
        A_count = np.zeros((n, n), dtype=float)
        for i in range(1, len(cluster_seq)):
            prev, curr = cluster_seq[i - 1], cluster_seq[i]
            # นับเฉพาะจุดที่ทั้งคู่อยู่ใน known cluster และเปลี่ยน cluster จริง
            if prev is not None and curr is not None and prev != curr:
                A_count[prev, curr] += 1

        # Normalize → transition probability (row stochastic)
        row_sums = A_count.sum(axis=1, keepdims=True)
        row_sums_safe = np.where(row_sums == 0, 1, row_sums)  # ป้องกัน division by zero
        self.transition_matrix = A_count / row_sums_safe

        # แถวที่ไม่มีข้อมูลเลย → ใช้ uniform distribution
        zero_rows = (A_count.sum(axis=1) == 0)
        self.transition_matrix[zero_rows] = 1.0 / n

        # ── Initial state probabilities ──────────────────────────────────────
        # นับจาก cluster แรกของแต่ละวัน
        init_count = np.zeros(n, dtype=float)
        current_day = None
        for cid, day in zip(cluster_seq, day_seq):
            if day != current_day:
                current_day = day
                if cid is not None:
                    init_count[cid] += 1

        total_init = init_count.sum()
        self.initial_probs = init_count / total_init if total_init > 0 else np.ones(n) / n

        # ── Historical routes (per day) ──────────────────────────────────────
        # ใช้เปรียบเทียบความคุ้นเคยของเส้นทางด้วย DTW ภายหลัง
        self._historical_routes = []
        day_routes: dict[str, list[int]] = {}
        for cid, day in zip(cluster_seq, day_seq):
            if cid is None:
                continue
            if day not in day_routes:
                day_routes[day] = []
            # เก็บเฉพาะเมื่อ cluster เปลี่ยน (เหมือน run-length encoding)
            if not day_routes[day] or day_routes[day][-1] != cid:
                day_routes[day].append(cid)

        self._historical_routes = [seq for seq in day_routes.values() if len(seq) >= 2]

        return {
            "status": "fitted",
            "n_clusters": n,
            "n_transitions": int(A_count.sum()),
            "n_historical_routes": len(self._historical_routes),
        }

    # ── predict_route ─────────────────────────────────────────────────────────

    def predict_route(
        self,
        recent_gps: list,
        destination_cluster_id: int,
        known_places: list[dict],
    ) -> dict:
        """
        ทำนายเส้นทางจาก GPS ปัจจุบัน → destination cluster

        Parameters
        ----------
        recent_gps             : GPS records ล่าสุด (แนะนำ 5–20 จุด)
        destination_cluster_id : cluster ปลายทางจาก DestinationPredictor
        known_places           : cluster dicts จาก BehavioralProfile

        Returns
        -------
        {
            "status": "ok",
            "predicted_route": [{"latitude", "longitude", "cluster_id"}],
            "waypoint_count": int,
            "similarity_score": float,   # 0.0–1.0
            "similarity_pct": int,       # 0–100
            "route_familiar": bool,      # True ถ้า score >= 0.6
        }
        """
        if self.transition_matrix is None:
            return {"status": "not_fitted", "message": "Call fit() before predict_route()."}
        if not recent_gps:
            return {"status": "no_gps", "message": "recent_gps is empty."}
        if not known_places:
            return {"status": "no_places", "message": "known_places is empty."}

        cluster_map = {p["cluster_id"]: p for p in known_places}

        # 1. Viterbi: GPS sequence → cluster sequence
        obs_lats = [_get_lat_lng(r)[0] for r in recent_gps]
        obs_lngs = [_get_lat_lng(r)[1] for r in recent_gps]
        viterbi_path = self._viterbi(obs_lats, obs_lngs, known_places)

        # cluster ปัจจุบัน = cluster สุดท้ายของ Viterbi path
        current_cluster = viterbi_path[-1] if viterbi_path else None

        # Edge case: ไม่รู้ตำแหน่งหรืออยู่ที่ destination แล้ว
        if current_cluster is None or current_cluster == destination_cluster_id:
            dest = cluster_map.get(destination_cluster_id, {})
            return {
                "status": "ok",
                "predicted_route": [{
                    "latitude": float(dest.get("latitude", obs_lats[-1])),
                    "longitude": float(dest.get("longitude", obs_lngs[-1])),
                    "cluster_id": int(destination_cluster_id),
                }],
                "waypoint_count": 1,
                "similarity_score": 1.0,
                "similarity_pct": 100,
                "route_familiar": True,
            }

        # 2. ต่อเส้นทางจาก current_cluster → destination ผ่าน transition matrix
        cluster_path = self._extend_to_destination(current_cluster, destination_cluster_id)

        # 3. แปลง cluster path → lat/lng waypoints
        waypoints = []
        for cid in cluster_path:
            info = cluster_map.get(int(cid), {})
            waypoints.append({
                "latitude":  float(info.get("latitude", 0.0)),
                "longitude": float(info.get("longitude", 0.0)),
                "cluster_id": int(cid),
            })

        # 4. DTW similarity เทียบกับ historical routes ที่ไปถึง destination เดิม
        predicted_coords = [(w["latitude"], w["longitude"]) for w in waypoints]
        relevant_routes = [
            r for r in self._historical_routes
            if r and r[-1] == destination_cluster_id
        ]

        if relevant_routes:
            scores = []
            for hist_seq in relevant_routes:
                hist_coords = [
                    (cluster_map[cid]["latitude"], cluster_map[cid]["longitude"])
                    for cid in hist_seq if cid in cluster_map
                ]
                if len(hist_coords) >= 2:
                    scores.append(self._dtw_similarity(predicted_coords, hist_coords))
            similarity = float(np.mean(scores)) if scores else 0.0
        else:
            # ไม่มีเส้นทางเก่าไปถึง destination นี้ → ไม่คุ้นเคย
            similarity = 0.0

        similarity = round(max(0.0, min(1.0, similarity)), 3)

        return {
            "status": "ok",
            "predicted_route": waypoints,
            "waypoint_count": len(waypoints),
            "similarity_score": similarity,
            "similarity_pct": round(similarity * 100),
            "route_familiar": similarity >= 0.6,
        }

    # ── _viterbi ──────────────────────────────────────────────────────────────

    def _viterbi(self, obs_lats: list[float], obs_lngs: list[float],
                 known_places: list[dict]) -> list[int]:
        """
        Viterbi decoding: GPS observations → cluster sequence ที่น่าจะเป็นที่สุด

        ใช้ log-space เพื่อป้องกัน underflow ของ probability เล็ก ๆ
        """
        T = len(obs_lats)
        n = self.n_clusters
        if T == 0 or n == 0:
            return []

        log_A  = np.log(self.transition_matrix + 1e-12)   # (n, n)
        log_pi = np.log(self.initial_probs + 1e-12)        # (n,)

        # viterbi[t, s] = log prob ของ best path ที่จบที่ state s ณ เวลา t
        viterbi = np.full((T, n), -np.inf)
        backptr = np.zeros((T, n), dtype=int)

        # t = 0: initial
        log_e0 = np.array([
            self._log_emission(obs_lats[0], obs_lngs[0], s, known_places)
            for s in range(n)
        ])
        viterbi[0] = log_pi + log_e0

        # t = 1..T-1: recursion
        for t in range(1, T):
            log_e = np.array([
                self._log_emission(obs_lats[t], obs_lngs[t], s, known_places)
                for s in range(n)
            ])
            for s in range(n):
                # viterbi[t-1, prev] + log_A[prev, s] → เลือก prev ที่ดีที่สุด
                trans_scores = viterbi[t - 1] + log_A[:, s]
                best_prev = int(np.argmax(trans_scores))
                viterbi[t, s] = trans_scores[best_prev] + log_e[s]
                backptr[t, s] = best_prev

        # Backtrack
        path = [int(np.argmax(viterbi[T - 1]))]
        for t in range(T - 1, 0, -1):
            path.append(int(backptr[t, path[-1]]))
        path.reverse()
        return path

    def _log_emission(self, lat: float, lng: float, cluster_id: int,
                      known_places: list[dict]) -> float:
        """
        Log P(GPS obs | state = cluster_id)
        ใช้ Gaussian บน haversine distance ถึง centroid ของ cluster
        """
        for p in known_places:
            if p["cluster_id"] == cluster_id:
                dist_m = haversine_km(lat, lng, p["latitude"], p["longitude"]) * 1000.0
                # log ของ Gaussian (unnormalized) → ยิ่งใกล้ centroid ยิ่งสูง
                return -0.5 * (dist_m / self.emission_sigma_m) ** 2
        return -1e6  # ไม่พบ cluster → probability ≈ 0

    # ── _extend_to_destination ────────────────────────────────────────────────

    def _extend_to_destination(self, start: int, dest: int) -> list[int]:
        """
        ต่อเส้นทางจาก start → dest โดยใช้ transition matrix แบบ greedy
        ป้องกัน cycle ด้วย visited set

        ถ้า destination มี direct transition → ไปทันที
        ถ้าติดตัน (ไม่มี unvisited neighbors) → กระโดดตรงไป dest
        """
        path = [start]
        visited = {start}

        for _ in range(self.max_hops):
            current = path[-1]
            if current == dest:
                break

            row = self.transition_matrix[current].copy()

            # ถ้า destination มี direct transition → ไปเลย
            if row[dest] > 0:
                path.append(dest)
                break

            # ปิด visited nodes
            row[list(visited)] = 0.0

            total = row.sum()
            if total == 0.0:
                # ไม่มีทางออก → กระโดดตรง
                path.append(dest)
                break

            next_cluster = int(np.argmax(row))
            path.append(next_cluster)
            visited.add(next_cluster)

        # รับประกันว่า destination อยู่ท้ายสุดเสมอ
        if path[-1] != dest:
            path.append(dest)

        return path

    # ── _dtw_similarity ───────────────────────────────────────────────────────

    def _dtw_similarity(self, route_a: list[tuple], route_b: list[tuple]) -> float:
        """
        คำนวณ Route Similarity Score จาก DTW distance (0.0–1.0)

        ใช้ haversine distance (km) ระหว่าง waypoints
        Normalize ด้วยความยาวเส้นทางที่ยาวกว่า → score ไม่ขึ้นกับ scale

        คืน 1.0 = เหมือนกันทุกจุด, 0.0 = ต่างกันมาก
        """
        n, m = len(route_a), len(route_b)
        if n == 0 or m == 0:
            return 0.0
        if n == 1 and m == 1:
            dist = haversine_km(route_a[0][0], route_a[0][1],
                                route_b[0][0], route_b[0][1])
            return math.exp(-dist)

        # DTW cost matrix
        dtw = np.full((n, m), np.inf)
        dtw[0, 0] = haversine_km(route_a[0][0], route_a[0][1],
                                  route_b[0][0], route_b[0][1])
        for i in range(1, n):
            dtw[i, 0] = dtw[i - 1, 0] + haversine_km(
                route_a[i][0], route_a[i][1], route_b[0][0], route_b[0][1])
        for j in range(1, m):
            dtw[0, j] = dtw[0, j - 1] + haversine_km(
                route_a[0][0], route_a[0][1], route_b[j][0], route_b[j][1])
        for i in range(1, n):
            for j in range(1, m):
                cost = haversine_km(
                    route_a[i][0], route_a[i][1], route_b[j][0], route_b[j][1])
                dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])

        dtw_dist = dtw[n - 1, m - 1]

        # Normalize: ประมาณ "ระยะทางสูงสุดที่เป็นไปได้" = ความยาวเส้นทางที่ยาวกว่า
        def _route_len(route: list[tuple]) -> float:
            return sum(
                haversine_km(route[k - 1][0], route[k - 1][1],
                             route[k][0], route[k][1])
                for k in range(1, len(route))
            ) or 1e-6

        max_len = max(_route_len(route_a), _route_len(route_b))
        # similarity = e^(-dtw/max_len): ยิ่ง DTW น้อย similarity ยิ่งสูง
        return math.exp(-dtw_dist / max_len)
