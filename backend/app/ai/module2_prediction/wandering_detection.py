"""Module 2.3 — Wandering Detection using Isolation Forest.

ตรวจจับพฤติกรรมเดินวนหรือหลงทางจาก GPS trajectory ของผู้ป่วย

Design: Sync / pipeline style — รับ data ที่เตรียมไว้แล้ว ไม่มี DB call ใน class

──────────────────────────────────────────────────────────────────────────
Algorithm: Isolation Forest (Anomaly Detection)
  ฝึกบน features ของการเดิน "ปกติ" จาก GPS history 30 วัน
  แล้ว score GPS ล่าสุด → anomaly = wandering

Features ต่อ window:
  1. avg_speed_ms      : ความเร็วเฉลี่ย (m/s)
  2. std_speed         : ความผันผวนของความเร็ว
  3. direction_changes : จำนวนครั้งที่ทิศเปลี่ยน > 45° (วนเวียน = เยอะ)
  4. movement_radius_km: ระยะสูงสุดจาก centroid (circle of movement)
  5. displacement_ratio: อัตราส่วน path_length / straight_displacement
                         (1.0 = เดินตรง, สูง = วนซ้ำ/เดินย้อน)
──────────────────────────────────────────────────────────────────────────

Usage (pipeline หลัง RoutePredictor):
---------------------------------------
    detector = WanderingDetector()
    detector.fit(gps_records_30d, known_places)

    result = detector.detect(recent_gps=gps_records[-30:])
    # result["wandering_detected"]  → True / False
    # result["wandering_score"]     → 0.0–1.0
    # result["wandering_level"]     → "normal" | "mild" | "high"
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest

from app.ai.module2_prediction.cluster_matcher import (
    haversine_km, bearing as _bearing, angle_diff as _angle_diff,
    get_lat_lng as _get_lat_lng, get_speed as _get_speed,
)

# ─── Thresholds ─────────────────────────────────────────────────────────────
# anomaly score จาก Isolation Forest อยู่ใน [-1, 1]
# sklearn คืนค่า negative = anomaly → แปลงเป็น 0–1 ด้านล่าง
_MILD_THRESHOLD = 0.55   # wandering_score >= 0.55 → mild
_HIGH_THRESHOLD = 0.75   # wandering_score >= 0.75 → high risk

# ขนาด window สำหรับ feature extraction (จำนวน GPS points)
_WINDOW_SIZE = 15


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_timestamp(r):
    """คืน recorded_at หรือ timestamp จาก ORM object หรือ dict"""
    if isinstance(r, dict):
        return r.get("recorded_at") or r.get("timestamp")
    return getattr(r, "recorded_at", getattr(r, "timestamp", None))


# ─────────────────────────────────────────────────────────────────────────────
# Feature extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_features(gps_window: list) -> np.ndarray | None:
    """
    แปลง GPS window → feature vector ขนาด 5

    คืน None ถ้า window สั้นเกินไป (< 3 จุด)
    """
    if len(gps_window) < 3:
        return None

    lats = np.array([_get_lat_lng(r)[0] for r in gps_window])
    lngs = np.array([_get_lat_lng(r)[1] for r in gps_window])
    speeds = [_get_speed(r) for r in gps_window]
    speeds_valid = [s for s in speeds if s is not None and s >= 0]

    # 1. avg_speed & std_speed
    if speeds_valid:
        avg_speed = float(np.mean(speeds_valid))
        std_speed = float(np.std(speeds_valid))
    else:
        # ประมาณจาก haversine ระหว่างจุดติดกัน (ไม่รู้ interval → หน่วยสัมพัทธ์)
        dists = [
            haversine_km(lats[i], lngs[i], lats[i + 1], lngs[i + 1]) * 1000
            for i in range(len(lats) - 1)
        ]
        avg_speed = float(np.mean(dists)) if dists else 0.0
        std_speed = float(np.std(dists)) if dists else 0.0

    # 2. direction_changes: จำนวนครั้งที่ bearing เปลี่ยน > 45°
    bearings = [
        _bearing(lats[i], lngs[i], lats[i + 1], lngs[i + 1])
        for i in range(len(lats) - 1)
        if haversine_km(lats[i], lngs[i], lats[i + 1], lngs[i + 1]) > 0.005  # > 5 m
    ]
    direction_changes = sum(
        1 for i in range(1, len(bearings))
        if _angle_diff(bearings[i], bearings[i - 1]) > 45
    )

    # 3. movement_radius_km: ระยะสูงสุดจาก centroid
    centroid_lat = float(np.mean(lats))
    centroid_lng = float(np.mean(lngs))
    movement_radius_km = max(
        haversine_km(lat, lng, centroid_lat, centroid_lng)
        for lat, lng in zip(lats, lngs)
    )

    # 4. displacement_ratio: path_length / straight_displacement
    path_length_km = sum(
        haversine_km(lats[i], lngs[i], lats[i + 1], lngs[i + 1])
        for i in range(len(lats) - 1)
    )
    straight_km = haversine_km(lats[0], lngs[0], lats[-1], lngs[-1])
    if straight_km > 0.0002:  # > 20 cm
        displacement_ratio = path_length_km / straight_km
    else:
        # ถ้าเดินพอสมควร (> 2 เมตร) แต่อยู่ที่เดิม = วนเวียนจัดๆ (outlier)
        displacement_ratio = 10.0 if path_length_km > 0.002 else 1.0
    displacement_ratio = min(displacement_ratio, 10.0)  # cap ไม่ให้ outlier ควบคุม model

    return np.array([
        avg_speed,
        std_speed,
        float(direction_changes),
        movement_radius_km,
        displacement_ratio,
    ], dtype=float)


def _window_features(gps_records: list, window_size: int) -> np.ndarray:
    """สร้าง feature matrix จาก sliding windows บน GPS records โดยแยกกลุ่ม (session) ตามเวลา/ระยะทาง."""
    if not gps_records:
        return np.empty((0, 5))

    # 1. แบ่ง gps_records เป็น session ย่อยๆ เมื่อเจอกระโดดของระยะทาง (> 200m) หรือเวลา (> 5 นาที)
    sessions = []
    current_session = []

    for r in gps_records:
        if not current_session:
            current_session.append(r)
            continue

        prev_r = current_session[-1]
        lat1, lng1 = _get_lat_lng(prev_r)
        lat2, lng2 = _get_lat_lng(r)

        is_split = False

        # ระยะทางกระโดด > 200 เมตร ถือว่าคนละเที่ยวการเดินทาง / teleportation
        if haversine_km(lat1, lng1, lat2, lng2) > 0.2:
            is_split = True

        # เวลาห่างกัน > 5 นาที
        t1 = _get_timestamp(prev_r)
        t2 = _get_timestamp(r)
        if t1 and t2:
            try:
                if isinstance(t1, str):
                    t1 = datetime.fromisoformat(t1.replace("Z", "+00:00"))
                if isinstance(t2, str):
                    t2 = datetime.fromisoformat(t2.replace("Z", "+00:00"))
                if abs((t2 - t1).total_seconds()) > 300:
                    is_split = True
            except Exception:
                pass

        if is_split:
            if len(current_session) >= window_size:
                sessions.append(current_session)
            current_session = [r]
        else:
            current_session.append(r)

    if len(current_session) >= window_size:
        sessions.append(current_session)

    # 2. ทำ sliding window feature extraction จากแต่ละ session แยกกัน
    rows = []
    step = max(1, window_size // 2)
    for sess in sessions:
        for start in range(0, len(sess) - window_size + 1, step):
            window = sess[start : start + window_size]
            feat = _extract_features(window)
            if feat is not None:
                rows.append(feat)

    return np.array(rows) if rows else np.empty((0, 5))


# ─────────────────────────────────────────────────────────────────────────────
# WanderingDetector
# ─────────────────────────────────────────────────────────────────────────────

class WanderingDetector:
    """
    ตรวจจับพฤติกรรมหลงทาง/เดินวนจาก GPS trajectory

    Parameters
    ----------
    contamination : สัดส่วนที่คาดว่าเป็น anomaly ใน training data (0.0–0.5)
                    0.05 = คาดว่า ~5% ของ GPS history เป็น "ผิดปกติ"
    window_size   : จำนวน GPS points ต่อ feature window
    """

    def __init__(self, contamination: float = 0.05, window_size: int = _WINDOW_SIZE):
        self.contamination = contamination
        self.window_size = window_size
        self._model: Optional[IsolationForest] = None
        self._feature_mean: Optional[np.ndarray] = None  # fallback ถ้า model ไม่ถูก fit

    # ── fit ───────────────────────────────────────────────────────────────────

    def fit(self, gps_records: list, known_places: list | None = None) -> dict:
        """
        ฝึก Isolation Forest บน GPS history ที่ถือว่าเป็น "ปกติ"

        Parameters
        ----------
        gps_records  : GPS history 30 วัน (ORM objects หรือ dicts)
        known_places : ไม่บังคับ — ไม่ได้ใช้ใน v1 แต่เปิดไว้สำหรับอนาคต

        Returns
        -------
        dict สรุปผล fit
        """
        if len(gps_records) < self.window_size * 2:
            return {
                "status": "skipped",
                "reason": f"need >= {self.window_size * 2} GPS records, got {len(gps_records)}",
            }

        X = _window_features(gps_records, self.window_size)
        if X.shape[0] < 5:
            return {"status": "skipped", "reason": "not enough valid windows"}

        self._feature_mean = X.mean(axis=0)

        self._model = IsolationForest(
            contamination=self.contamination,
            n_estimators=100,
            random_state=42,
        )
        self._model.fit(X)

        return {
            "status": "fitted",
            "n_windows": int(X.shape[0]),
            "n_gps_records": len(gps_records),
        }

    # ── detect ────────────────────────────────────────────────────────────────

    def detect(self, recent_gps: list) -> dict:
        """
        ตรวจ wandering จาก GPS ล่าสุด

        Parameters
        ----------
        recent_gps : GPS records ล่าสุด (แนะนำ 15–30 จุด)

        Returns
        -------
        {
            "status": "ok",
            "wandering_detected": bool,
            "wandering_score": float,   # 0.0–1.0 (สูง = น่ากังวล)
            "wandering_level": str,     # "normal" | "mild" | "high"
            "features": {               # raw features สำหรับ debug / Module 3
                "avg_speed_ms", "std_speed", "direction_changes",
                "movement_radius_km", "displacement_ratio"
            }
        }
        """
        if not recent_gps:
            return {"status": "no_gps", "message": "recent_gps is empty."}

        # Extract features จาก window ล่าสุด
        window = recent_gps[-self.window_size:]
        feat = _extract_features(window)

        if feat is None:
            return {"status": "insufficient_data", "message": "Need at least 3 GPS points."}

        feat_dict = {
            "avg_speed_ms":      round(float(feat[0]), 3),
            "std_speed":         round(float(feat[1]), 3),
            "direction_changes": int(feat[2]),
            "movement_radius_km": round(float(feat[3]), 4),
            "displacement_ratio": round(float(feat[4]), 3),
        }

        # ── Score ─────────────────────────────────────────────────────────────
        if self._model is not None:
            # ใช้ decision_function ของ Isolation Forest:
            # - ค่าเป็นบวก (ปกติสูงสุด ≈ 0.4) => normal (inlier)
            # - ค่าเป็นลบ (ต่ำสุด ≈ -0.5) => anomaly (outlier / wandering)
            df_score = float(self._model.decision_function([feat])[0])
            
            # แปลงอย่างสมูทเป็นคะแนน 0–1 ด้วย sigmoid-like exponential mapping:
            # - df_score = 0 (รอยต่อ/borderline) => score = 0.5
            # - df_score > 0 => score อยู่ในช่วง [0.0, 0.5]
            # - df_score < 0 => score อยู่ในช่วง [0.5, 1.0]
            if df_score >= 0:
                wandering_score = float(0.5 * math.exp(-df_score / 0.15))
            else:
                wandering_score = float(0.5 + 0.5 * (1.0 - math.exp(df_score / 0.15)))
            
            wandering_score = float(np.clip(wandering_score, 0.0, 1.0))
        else:
            # Fallback: rule-based เมื่อยังไม่ได้ fit (ไม่มี history เพียงพอ)
            wandering_score = self._rule_based_score(feat)

        wandering_score = round(wandering_score, 3)

        # ── Level ─────────────────────────────────────────────────────────────
        if wandering_score >= _HIGH_THRESHOLD:
            level = "high"
        elif wandering_score >= _MILD_THRESHOLD:
            level = "mild"
        else:
            level = "normal"

        return {
            "status": "ok",
            "wandering_detected": bool(wandering_score >= _MILD_THRESHOLD),
            "wandering_score":    wandering_score,
            "wandering_pct":      round(wandering_score * 100),
            "wandering_level":    level,
            "features":           feat_dict,
        }

    # ── rule-based fallback ───────────────────────────────────────────────────

    def _rule_based_score(self, feat: np.ndarray) -> float:
        """
        Fallback เมื่อ Isolation Forest ยังไม่ถูก fit
        ใช้ rule เกณฑ์เดิม:
          - direction_changes สูง → คะแนนขึ้น
          - displacement_ratio สูง (วนมาก) → คะแนนขึ้น
          - movement_radius เล็ก + direction_changes เยอะ → wandering
        """
        avg_speed, std_speed, dir_changes, radius_km, disp_ratio = feat

        score = 0.0
        # เดินวน: direction เปลี่ยนบ่อย
        score += min(dir_changes / 10.0, 0.4)
        # displacement ratio สูง = เดินย้อนกลับ/วน
        score += min((disp_ratio - 1.0) / 5.0, 0.3)
        # อยู่ในพื้นที่เล็กแต่เดินนาน (radius เล็กมาก)
        if radius_km < 0.05 and dir_changes >= 3:
            score += 0.2
        # ความเร็วต่ำมาก + movement เยอะ
        if avg_speed < 0.3 and dir_changes >= 5:
            score += 0.1

        return min(score, 1.0)
