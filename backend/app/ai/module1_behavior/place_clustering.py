# pathguard/backend/app/ai/module1_behavior/place_clustering.py

import math

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

EARTH_RADIUS_M = 6_371_000  # รัศมีเฉลี่ยของโลก ใช้แปลงเมตร <-> เรเดียน

# Stay-point extraction settings (Li et al.). Measured 2026-08-22: feeding raw
# GPS fixes straight into DBSCAN (the old approach) turned a 30-day GeoLife
# window into 156 "places", 124 of them under 5 minutes average stay — traffic
# lights and roads, not destinations. Collapsing the track to stay points first
# cuts that to 6-8 real places on the same data. See
# scripts/measure_learning_days.py --stops for the original measurement.
_STAY_RADIUS_M = 100.0
_STAY_MINUTES = 15.0
# Counts real visits once fixes are collapsed to stops, not raw GPS fixes — a
# fix-based min_samples=5 on a 20s track meant "stood still 100 seconds";
# visit-based min_visits=2 means "came back at least once in 30 days".
_MIN_VISITS = 2


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres between two lat/lng points."""
    r = EARTH_RADIUS_M
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def extract_stay_points(
    df: pd.DataFrame,
    radius_m: float = _STAY_RADIUS_M,
    minutes: float = _STAY_MINUTES,
) -> pd.DataFrame:
    """Collapse a GPS track to one row per *stop* — a run of consecutive fixes
    that stayed within ``radius_m`` of each other for at least ``minutes``.

    Standard stay-point detection (Li et al.): a destination is a place the
    patient stood still at for a while, not any spot with enough fixes nearby.
    Feeding every raw fix to DBSCAN can't tell a red light from a home, because
    a dense 20-second-interval track produces plenty of fixes at either.

    Each stay point also carries how long that stop lasted, so a later cluster
    of stay points can report a real average stay time instead of a stub.
    """
    lat = df["latitude"].to_numpy()
    lng = df["longitude"].to_numpy()
    ts = df["timestamp"].to_numpy()
    n = len(df)

    stays, i = [], 0
    while i < n:
        j = i + 1
        while j < n and _haversine_m(lat[i], lng[i], lat[j], lng[j]) <= radius_m:
            j += 1
        span_s = (ts[j - 1] - ts[i]) / pd.Timedelta(seconds=1)
        if span_s >= minutes * 60:
            stays.append({
                "latitude": float(lat[i:j].mean()),
                "longitude": float(lng[i:j].mean()),
                "duration_minutes": span_s / 60.0,
                "timestamp": ts[i],
            })
            i = j
        else:
            i += 1

    return pd.DataFrame(stays, columns=["latitude", "longitude", "duration_minutes", "timestamp"])


def cluster_places(df: pd.DataFrame) -> list:
    """
    หาสถานที่ที่ผู้ป่วยไปบ่อย จากข้อมูล GPS 30 วันย้อนหลัง

    Input  : DataFrame ที่มีคอลัมน์ lat, lng, timestamp (ต้องมี timestamp เพื่อหา
             stay point — ถ้าไม่มี ให้ผลเป็น [] แทนที่จะยัดจุดดิบเข้า DBSCAN)
    Output : รายการสถานที่สำคัญ — clustered จาก *stay points* ไม่ใช่จุด GPS ดิบ
    """
    if "timestamp" not in df.columns or df.empty:
        return []

    stays = extract_stay_points(df.sort_values("timestamp"))
    if len(stays) < _MIN_VISITS:
        return []

    # DBSCAN ใช้ metric="haversine" ซึ่งคิดระยะเป็น "เรเดียน" ดังนั้น eps ต้องแปลงจากเมตร
    eps = 50 / EARTH_RADIUS_M
    coords = stays[["latitude", "longitude"]].values
    labels = DBSCAN(eps=eps, min_samples=_MIN_VISITS, metric="haversine").fit(
        np.radians(coords)
    ).labels_

    results = []
    for cluster_id in sorted(set(labels)):
        if cluster_id == -1:  # -1 คือ noise ข้ามไป
            continue

        rows = stays[labels == cluster_id]
        results.append({
            "cluster_id": int(cluster_id),
            "latitude": float(rows["latitude"].mean()),
            "longitude": float(rows["longitude"].mean()),
            "visit_frequency": len(rows),  # จำนวนครั้งที่แวะ ไม่ใช่จำนวนจุด GPS
            "avg_stay_time": round(float(rows["duration_minutes"].mean()), 1),
        })

    return results
