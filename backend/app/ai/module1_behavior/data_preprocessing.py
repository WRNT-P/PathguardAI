# pathguard/backend/app/ai/module1_behavior/data_preprocessing.py

import numpy as np
import pandas as pd
from app.services.kalman_filter import KalmanFilter

def preprocess_gps(df: pd.DataFrame) -> pd.DataFrame:
    """
    ทำความสะอาดข้อมูล GPS ก่อนส่งให้ DBSCAN และ LSTM
    Input  : DataFrame ที่มีคอลัมน์ lat, lng, speed, timestamp
    Output : DataFrame ที่สะอาดแล้ว
    """

    # 1. ลบแถวที่ข้อมูลขาด
    df = df.dropna(subset=["latitude", "longitude", "speed"])

    # 2. แปลง timestamp เป็น datetime
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # 3. แปลงความเร็วเป็น m/s
    df["speed"] = df["speed"].clip(lower=0)

    # 4. ใช้ Kalman Filter ลด GPS Noise
    kf = KalmanFilter()
    df["latitude"], df["longitude"] = kf.smooth(
        df["latitude"].values,
        df["longitude"].values
    )

    # 5. ใช้ Moving Average ลดความผันผวนของความเร็ว
    df["speed"] = df["speed"].rolling(window=5, min_periods=1).mean()

    return df