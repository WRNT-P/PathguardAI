"""Module 2.4 — Stop vs Confusion Classification.

จำแนกสถานการณ์การหยุดนิ่งของผู้ป่วย:
  - Normal Stop (หยุดจงใจ): เช่น หยุดพักเหนื่อย, ซื้อของที่ร้านประจำ, อยู่บ้าน (familiarity สูง, ห่างเส้นทางน้อย)
  - Confusion Stop (หยุดสับสน): เช่น หยุดนิ่งกลางทางเดินที่ไม่รู้จักหลังจากเดินเลี้ยวไปเลี้ยวมา (familiarity ต่ำ, deviation สูง, มีเลี้ยวบ่อยก่อนหยุด)

ใช้ Gradient Boosting Classifier ในการสร้างโมเดลจำแนก
"""

from __future__ import annotations

import math
from typing import Optional
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier

from app.ai.module2_prediction.cluster_matcher import haversine_km

# ─── Thresholds ─────────────────────────────────────────────────────────────
_CONFUSION_THRESHOLD = 0.60  # ถ้า confidence >= 0.60 -> confused


def _bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """คำนวณ bearing ทิศทางจากจุด 1 -> 2 (0-360 องศา)"""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    x = math.sin(dl) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def _get_lat_lng(r) -> tuple[float, float]:
    if isinstance(r, dict):
        return float(r["latitude"]), float(r["longitude"])
    return float(r.latitude), float(r.longitude)


def _get_speed(r) -> float | None:
    if isinstance(r, dict):
        return r.get("speed")
    return getattr(r, "speed", None)


class StopConfusionClassifier:
    """
    วิเคราะห์และจำแนกประเภทการหยุดนิ่ง (Stop) ของผู้ป่วย
    """

    def __init__(self):
        self._model: Optional[GradientBoostingClassifier] = None
        self._is_trained = False

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

        # 4. Familiarity score (อิงตามระยะห่างจากสถานที่คุ้นเคยที่ใกล้ที่สุด)
        familiarity = 0.0
        if known_places:
            min_dist = float('inf')
            for kp in known_places:
                klat, klng = _get_lat_lng(kp)
                dist = haversine_km(current_lat, current_lng, klat, klng)
                if dist < min_dist:
                    min_dist = dist
            
            # ถ้าใกล้ที่คุ้นเคยในระยะ 100m -> familiarity สูง (1.0 ที่ 0m, 0.0 ที่ >= 100m)
            if min_dist <= 0.1:
                familiarity = 1.0 - (min_dist / 0.1)

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
            # ถ้าไม่มีเส้นทางแนะนำที่คาดเดาได้ (อาจจะกำลังหลงทางตั้งแต่แรก)
            deviation_m = 300.0  # ให้เป็นค่าผิดปกติเบื้องต้น

        return np.array([
            stop_dur,
            avg_speed,
            float(dir_changes),
            familiarity,
            deviation_m
        ], dtype=float)

    # ── fit model ─────────────────────────────────────────────────────────────

    def fit(self, X_train: np.ndarray, y_train: np.ndarray) -> dict:
        """
        ฝึก Gradient Boosting Classifier ด้วยชุดข้อมูลที่ระบุ Label มาให้

        Parameters
        ----------
        X_train : Feature matrix (N, 5)
        y_train : Labels (N,) -> 0 = Normal Stop, 1 = Confused Stop
        """
        if X_train.shape[0] < 5:
            return {"status": "error", "message": "Need at least 5 samples to fit model."}

        self._model = GradientBoostingClassifier(
            n_estimators=50,
            learning_rate=0.1,
            max_depth=3,
            random_state=42
        )
        self._model.fit(X_train, y_train)
        self._is_trained = True

        return {
            "status": "trained",
            "n_samples": int(X_train.shape[0]),
        }

    def fit_synthetic(self) -> dict:
        """
        ฝึกโมเดลด้วยการจำลองข้อมูลขึ้นมา (กรณีใช้งานแรกเริ่มที่ไม่มี Label ประวัติ)
        """
        # สร้างสถานการณ์ Normal (0) และ Confused (1) อย่างละ 50 ตัวอย่าง
        np.random.seed(42)
        
        # 1. Normal Stops (หยุดสั้น, มีความเร็วปกติก่อนหยุด, เลี้ยวน้อย, คุ้นเคยสูง, ห่างเส้นทางน้อย)
        normal_samples = []
        for _ in range(50):
            normal_samples.append([
                np.random.uniform(30.0, 180.0),     # หยุด 30-180 วินาที
                np.random.uniform(1.0, 1.6),        # ความเร็วก่อนหยุดปกติ 1.0-1.6 m/s
                np.random.choice([0, 1]),           # เลี้ยวก่อนหยุด 0-1 ครั้ง
                np.random.uniform(0.7, 1.0),        # ความคุ้นเคยสถานที่สูง 0.7-1.0
                np.random.uniform(0.0, 20.0),       # ห่างจากเส้นทางแค่ 0-20 เมตร
            ])
            
        # 2. Confused Stops (หยุดนานมาก, เดินช้าสับสนก่อนหยุด, เลี้ยวไปมาเยอะ, ไม่คุ้นเคย, ห่างเส้นทางมาก)
        confused_samples = []
        for _ in range(50):
            confused_samples.append([
                np.random.uniform(300.0, 1200.0),   # หยุดนาน 5-20 นาที
                np.random.uniform(0.3, 0.8),        # เดินช้าๆ ก่อนหยุด
                np.random.randint(3, 7),            # เลี้ยวสะเปะสะปะ 3-6 ครั้ง
                np.random.uniform(0.0, 0.2),        # ไม่คุ้นสถานที่ 0.0-0.2
                np.random.uniform(80.0, 400.0),     # นอกเส้นทางที่คาดหมาย 80-400 เมตร
            ])
            
        X = np.vstack([normal_samples, confused_samples])
        y = np.array([0] * 50 + [1] * 50)
        
        return self.fit(X, y)

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

        # คำนวณความน่าจะเป็นของ Anomaly (Class 1)
        if self._is_trained and self._model is not None:
            # predict_proba คืนค่า [[prob_normal, prob_confused]]
            probs = self._model.predict_proba([feat])[0]
            confidence_score = float(probs[1])
        else:
            # Fallback: ใช้กฎเชิงคณิตศาสตร์หากโมเดลยังไม่ถูกฝึกฝน
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
        คำนวณเกณฑ์สับสนด้วยคะแนนสัญชาตญาณ (เมื่อไม่ได้ฝึกโมเดล)
        """
        stop_dur, avg_speed, dir_changes, familiarity, deviation_m = feat

        score = 0.0
        
        # 1. หยุดนาน > 5 นาที (300s) -> ยิ่งนานยิ่งน่าสับสน
        score += min(stop_dur / 900.0, 0.3)
        
        # 2. ความเร็วก่อนหยุด (ต่ำมาก = ลังเล, สูงมาก = ปกติ)
        if avg_speed < 0.6:
            score += 0.15
            
        # 3. เลี้ยวเยอะก่อนหยุด -> วนเวียนสับสน
        score += min(dir_changes * 0.08, 0.2)
        
        # 4. หยุดในที่ที่ไม่คุ้นเคย (Familiarity ต่ำ)
        score += (1.0 - familiarity) * 0.2
        
        # 5. ห่างจากเส้นทางคาดหมายมาก
        score += min(deviation_m / 250.0, 0.15)

        return min(score, 1.0)
