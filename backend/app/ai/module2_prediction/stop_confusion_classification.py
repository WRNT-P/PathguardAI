"""Module 2.4 — Stop vs Confusion Classification.

จำแนกสถานการณ์การหยุดนิ่งของผู้ป่วย:
  - Normal Stop (หยุดจงใจ): เช่น หยุดพักเหนื่อย, ซื้อของที่ร้านประจำ, อยู่บ้าน (familiarity สูง, ห่างเส้นทางน้อย)
  - Confusion Stop (หยุดสับสน): เช่น หยุดนิ่งกลางทางเดินที่ไม่รู้จักหลังจากเดินเลี้ยวไปเลี้ยวมา (familiarity ต่ำ, deviation สูง, มีเลี้ยวบ่อยก่อนหยุด)

ใช้กฎเชิงคณิตศาสตร์ (rule-based) ในการให้คะแนนความสับสน — ไม่ใช่โมเดล ML ที่ฝึกจากข้อมูล
"""

from __future__ import annotations

import numpy as np

from app.ai.module2_prediction.cluster_matcher import (
    haversine_km, bearing as _bearing, angle_diff as _angle_diff,
    get_lat_lng as _get_lat_lng, get_speed as _get_speed,
    familiarity_at as _familiarity_at,
)

# ─── Thresholds ─────────────────────────────────────────────────────────────
_CONFUSION_THRESHOLD = 0.60  # ถ้า confidence >= 0.60 -> confused


class StopConfusionClassifier:
    """
    วิเคราะห์และจำแนกประเภทการหยุดนิ่ง (Stop) ของผู้ป่วย
    """

    # Intentionally rule-based, NOT a trained ML model. The system has no labeled
    # "confused vs normal stop" ground-truth data, so a trained classifier would
    # have to learn from invented labels (which is exactly what the removed
    # fit_synthetic() did — it merely re-derived the hand-written _rule_based_score
    # below). A genuine ML classifier here would first require a caregiver-feedback
    # labeling pipeline to capture real labels. Until that exists, classify() scores
    # stops with the transparent heuristic in _rule_based_score().

    # ── feature extraction ──────────────────────────────────────────────────

    def extract_features(
        self,
        recent_gps: list,
        stop_duration_seconds: float,
        current_lat: float,
        current_lng: float,
        predicted_route: list[tuple[float, float]] | None,
        known_places: list[dict]
    ) -> np.ndarray:
        """
        แปลงข้อมูลแวดล้อมขณะหยุด -> Feature Vector ขนาด 5
        1. stop_duration_seconds
        2. avg_speed_before
        3. direction_changes_before
        4. familiarity_score
        5. route_deviation_meters
        """
        # 1. Stop duration
        stop_dur = float(stop_duration_seconds)

        # 2. Avg speed before stop (ดึงจาก 10 จุดล่าสุดก่อนหยุด)
        speeds = [_get_speed(r) for r in recent_gps]
        valid_speeds = [s for s in speeds if s is not None and s >= 0]
        if valid_speeds:
            avg_speed = float(np.mean(valid_speeds))
        else:
            # ประมาณจากระยะทางระหว่างจุด
            dists = []
            for i in range(len(recent_gps) - 1):
                lat1, lng1 = _get_lat_lng(recent_gps[i])
                lat2, lng2 = _get_lat_lng(recent_gps[i + 1])
                dists.append(haversine_km(lat1, lng1, lat2, lng2) * 1000.0)
            avg_speed = float(np.mean(dists)) if dists else 1.0

        # 3. Direction changes before stop
        lats = [_get_lat_lng(r)[0] for r in recent_gps]
        lngs = [_get_lat_lng(r)[1] for r in recent_gps]
        bearings = []
        for i in range(len(lats) - 1):
            if haversine_km(lats[i], lngs[i], lats[i + 1], lngs[i + 1]) > 0.003:  # > 3m
                bearings.append(_bearing(lats[i], lngs[i], lats[i + 1], lngs[i + 1]))

        dir_changes = 0
        for i in range(1, len(bearings)):
            if _angle_diff(bearings[i], bearings[i - 1]) > 45.0:
                dir_changes += 1

        # 4. Familiarity score — the SHARED definition, so this agrees with the
        #    risk formula's F factor. This used to be a local `min_dist <= 0.1`
        #    rule that ignored each pin's own radius_m, so a patient 279 m from
        #    a 400 m home pin read as familiarity 0.0 here while Module 3 read
        #    1.0 for the same point at the same instant (measured on live
        #    patient 44, 2026-09-08) — worth 0.20 of a score capped at 1.00.
        familiarity = _familiarity_at(current_lat, current_lng, known_places)

        # 5. Route deviation (ระยะห่างจากเส้นทางแนะนำที่คาดการณ์ไว้)
        deviation_m = 0.0
        if predicted_route:
            min_dev = float('inf')
            for wp in predicted_route:
                wlat, wlng = wp
                dist = haversine_km(current_lat, current_lng, wlat, wlng) * 1000.0
                if dist < min_dev:
                    min_dev = dist
            deviation_m = min_dev
        else:
            # No predicted route means there is nothing to be off-course from,
            # not that the patient is far off course. The old 300.0 constant is
            # above _rule_based_score's 250.0 divisor, so this term hit its full
            # 0.15 ceiling for every patient the route predictor could not fit —
            # the ordinary case, not the exception. Distance from familiar places
            # is already its own 30 %-weighted risk factor (route_deviation);
            # charging it again inside confusion double-counts one fact.
            deviation_m = 0.0

        return np.array([
            stop_dur,
            avg_speed,
            float(dir_changes),
            familiarity,
            deviation_m
        ], dtype=float)

    # ── classify ──────────────────────────────────────────────────────────────

    def classify(
        self,
        recent_gps: list,
        stop_duration_seconds: float,
        current_lat: float,
        current_lng: float,
        predicted_route: list[tuple[float, float]] | None,
        known_places: list[dict]
    ) -> dict:
        """
        วิเคราะห์การหยุดนิ่งปัจจุบันว่าเป็น Normal หรือ Confused

        Returns
        -------
        {
            "status": "normal" | "confused",
            "confidence_score": float,  # ความน่าจะเป็นของสภาวะสับสน (0.0-1.0)
            "features": { ... }
        }
        """
        feat = self.extract_features(
            recent_gps=recent_gps,
            stop_duration_seconds=stop_duration_seconds,
            current_lat=current_lat,
            current_lng=current_lng,
            predicted_route=predicted_route,
            known_places=known_places
        )

        feat_dict = {
            "stop_duration_seconds": round(float(feat[0]), 1),
            "speed_before_stop":     round(float(feat[1]), 3),
            "direction_changes":     int(feat[2]),
            "familiarity_score":     round(float(feat[3]), 3),
            "route_deviation_meters": round(float(feat[4]), 1)
        }

        # คำนวณคะแนนความสับสนด้วยกฎเชิงคณิตศาสตร์ (rule-based)
        confidence_score = self._rule_based_score(feat)

        confidence_score = round(confidence_score, 3)
        status = "confused" if confidence_score >= _CONFUSION_THRESHOLD else "normal"

        return {
            "status": status,
            "confidence_score": confidence_score,
            "features": feat_dict
        }

    # ── rule-based fallback ───────────────────────────────────────────────────

    def _rule_based_score(self, feat: np.ndarray) -> float:
        """
        คำนวณเกณฑ์สับสนด้วยคะแนนสัญชาตญาณจาก 5 ฟีเจอร์ (rule-based ล้วน)
        """
        stop_dur, avg_speed, dir_changes, familiarity, deviation_m = feat

        score = 0.0

        # How much of the "stopped a long time, barely moving" evidence counts.
        # A three-hour motionless stop is the definition of being at home and the
        # definition of being stranded — the place is what separates them, and
        # the module docstring already says so ("Normal Stop ... อยู่บ้าน"). Left
        # unscaled, a patient asleep in their own bed scored 0.45 before any
        # other term, which on live data reached the 1.00 ceiling and spent the
        # full 20 points of the C factor on somebody doing nothing wrong.
        strangeness = 1.0 - familiarity

        # 1. หยุดนาน > 5 นาที (300s) -> ยิ่งนานยิ่งน่าสับสน (ถ้าไม่ใช่ที่คุ้นเคย)
        score += min(stop_dur / 900.0, 0.3) * strangeness

        # 2. ความเร็วก่อนหยุด (ต่ำมาก = ลังเล, สูงมาก = ปกติ)
        if avg_speed < 0.6:
            score += 0.15 * strangeness
            
        # 3. เลี้ยวเยอะก่อนหยุด -> วนเวียนสับสน
        score += min(dir_changes * 0.08, 0.2)
        
        # 4. หยุดในที่ที่ไม่คุ้นเคย (Familiarity ต่ำ)
        score += (1.0 - familiarity) * 0.2
        
        # 5. ห่างจากเส้นทางคาดหมายมาก
        score += min(deviation_m / 250.0, 0.15)

        return min(score, 1.0)
