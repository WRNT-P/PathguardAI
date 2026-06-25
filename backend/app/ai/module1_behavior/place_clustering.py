# pathguard/backend/app/ai/module1_behavior/place_clustering.py

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

EARTH_RADIUS_M = 6_371_000  # รัศมีเฉลี่ยของโลก ใช้แปลงเมตร <-> เรเดียน


def _avg_stay_minutes(df: pd.DataFrame) -> dict:
    """
    คำนวณเวลาพำนักเฉลี่ย (นาที) ของแต่ละ cluster
    โดยแยกเป็น "การมาเยือนแต่ละครั้ง" (visit) ตามลำดับเวลา
    แทนที่จะรวมทุกครั้งเข้าด้วยกัน
    """
    if "timestamp" not in df.columns:
        return {}

    ordered = df.sort_values("timestamp")
    # เริ่มนับ visit ใหม่ทุกครั้งที่ cluster เปลี่ยนระหว่างจุดที่ติดกันตามเวลา
    visit_id = (ordered["cluster"] != ordered["cluster"].shift()).cumsum()

    durations: dict = {}
    for (cluster_id, _), visit in ordered.groupby([ordered["cluster"], visit_id]):
        if cluster_id == -1:  # noise ข้ามไป
            continue
        span = (visit["timestamp"].iloc[-1] - visit["timestamp"].iloc[0]).total_seconds() / 60
        durations.setdefault(cluster_id, []).append(span)

    return {cid: float(np.mean(spans)) for cid, spans in durations.items()}


def cluster_places(df: pd.DataFrame) -> list:
    """
    หาสถานที่ที่ผู้ป่วยไปบ่อย จากข้อมูล GPS 30 วันย้อนหลัง
    Input  : DataFrame ที่มีคอลัมน์ lat, lng (และ timestamp ถ้าต้องการเวลาพำนัก)
    Output : รายการสถานที่สำคัญ
    """

    coords = df[["latitude", "longitude"]].values

    # DBSCAN ใช้ metric="haversine" ซึ่งคิดระยะเป็น "เรเดียน" ดังนั้น eps ต้องแปลงจากเมตร
    # eps ≈ 50 เมตร, min_samples=5 ครั้งขึ้นไปถือว่าเป็นสถานที่สำคัญ
    eps = 50 / EARTH_RADIUS_M
    db = DBSCAN(eps=eps, min_samples=5, metric="haversine").fit(
        np.radians(coords)
    )

    df["cluster"] = db.labels_

    # เวลาพำนักเฉลี่ยต่อ cluster (แยกตามการมาเยือนแต่ละครั้ง)
    stay_minutes = _avg_stay_minutes(df)

    # สรุปแต่ละ cluster
    results = []
    for cluster_id in set(db.labels_):
        if cluster_id == -1:  # -1 คือ noise ข้ามไป
            continue

        cluster_data = df[df["cluster"] == cluster_id]

        results.append({
            "cluster_id": int(cluster_id),
            "latitude": cluster_data["latitude"].mean(),
            "longitude": cluster_data["longitude"].mean(),
            "visit_frequency": len(cluster_data),
            "avg_stay_time": round(stay_minutes.get(int(cluster_id), 0.0), 1)
        })

    return results
