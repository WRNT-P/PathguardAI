# pathguard/backend/app/ai/module1_behavior/place_clustering.py

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

def cluster_places(df: pd.DataFrame) -> list:
    """
    หาสถานที่ที่ผู้ป่วยไปบ่อย จากข้อมูล GPS 30 วันย้อนหลัง
    Input  : DataFrame ที่มีคอลัมน์ lat, lng
    Output : รายการสถานที่สำคัญ
    """

    coords = df[["latitude", "longitude"]].values

    # DBSCAN — eps=0.0005 ≈ 50 เมตร, min_samples=5 ครั้งขึ้นไปถือว่าเป็นสถานที่สำคัญ
    db = DBSCAN(eps=0.0005, min_samples=5, metric="haversine").fit(
        np.radians(coords)
    )

    df["cluster"] = db.labels_

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
            "avg_stay_time": cluster_data["duration"].mean()
            if "duration" in cluster_data.columns else 0
        })

    return results