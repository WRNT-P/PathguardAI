# pathguard/backend/app/ai/module1_behavior/data_preprocessing.py

import numpy as np
import pandas as pd
from app.services.kalman_batch import KalmanFilter


def preprocess_gps(df: pd.DataFrame, adaptive: bool = True) -> pd.DataFrame:
    """
    ทำความสะอาดข้อมูล GPS ก่อนส่งให้ DBSCAN และ LSTM

    Input  : DataFrame ที่มีคอลัมน์ lat, lng, speed, timestamp
    Output : DataFrame ที่สะอาดแล้ว

    Parameters
    ----------
    adaptive : bool (default True)
        True  → เปิด Jump Detection (แนะนำสำหรับข้อมูลที่มีหลายสถานที่ห่างกัน)
        False → ปิด Jump Detection  (เหมาะกับ GPS เส้นทางเดินต่อเนื่อง ที่ไม่ต้องการ
                                    ให้ filter กระโดดตามทันที เช่น hiking trail)

    หมายเหตุ Q/R:
        ค่าใหม่ Q=1e-4, R=5e-4 → Kalman Gain ≈ 0.17 → lag เหลือ ~6 จุด (เดิม ~18 จุด)
        ทำให้สถานที่ที่ห่างกัน ~100 m+ แยก cluster ได้ถูกต้อง
    """

    # 1. ลบแถวที่ข้อมูลขาด
    df = df.dropna(subset=["latitude", "longitude", "speed"])

    # 2. แปลง timestamp เป็น datetime
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # 3. แปลงความเร็วเป็น m/s
    df["speed"] = df["speed"].clip(lower=0)

    # 4. ใช้ Kalman Filter ลด GPS Noise
    #    adaptive=True → เปิด Jump Detection สำหรับการย้ายสถานที่จริง
    kf = KalmanFilter(adaptive=adaptive)
    df["latitude"], df["longitude"] = kf.smooth(
        df["latitude"].values,
        df["longitude"].values
    )

    # 5. ใช้ Moving Average ลดความผันผวนของความเร็ว
    df["speed"] = df["speed"].rolling(window=5, min_periods=1).mean()

    return df