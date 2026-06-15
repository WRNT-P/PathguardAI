# pathguard/backend/app/services/kalman_batch.py
#
# Batch Kalman Filter สำหรับลด GPS Noise ของข้อมูลย้อนหลังทั้งก้อน (เช่น 30 วัน)
# ใช้ 1D Kalman Filter แยกกันสำหรับ latitude และ longitude
#
# หมายเหตุ: สำหรับ smoothing GPS แบบ "สด" ทีละจุด ให้ใช้ app.services.kalman_filter
#           ไฟล์นี้ใช้กับ AI module 1 (data_preprocessing) ที่ประมวลผลเป็น DataFrame
#
# หลักการ:
#   - State     : ค่า GPS ที่ "แท้จริง" ที่เราประมาณ
#   - Process noise (Q) : ความไม่แน่นอนของการเคลื่อนที่
#   - Measurement noise (R) : ความไม่แน่นอนของ GPS sensor
#   - P : ค่าความไม่แน่นอนของ state ปัจจุบัน

import numpy as np


class KalmanFilter:
    """
    1D Kalman Filter สำหรับ smooth ข้อมูล GPS (lat/lng)

    Parameters
    ----------
    process_noise (Q)   : ยิ่งสูง → ยืดหยุ่นกับการเปลี่ยนแปลงมาก (ตามการเคลื่อนที่เร็ว)
    measurement_noise (R): ยิ่งสูง → ไม่ค่อยเชื่อ sensor (smooth มากขึ้น)
    """

    def __init__(self, process_noise: float = 1e-5, measurement_noise: float = 1e-3):
        self.Q = process_noise
        self.R = measurement_noise

    def _filter_1d(self, measurements: np.ndarray) -> np.ndarray:
        """
        รัน Kalman Filter บน array 1 มิติ
        """
        n = len(measurements)
        filtered = np.zeros(n)

        # Initial state
        x = measurements[0]   # State estimate (เริ่มจากค่าแรก)
        P = 1.0               # Initial uncertainty

        for i, z in enumerate(measurements):
            # --- Predict ---
            x_pred = x
            P_pred = P + self.Q

            # --- Update ---
            K = P_pred / (P_pred + self.R)   # Kalman Gain
            x = x_pred + K * (z - x_pred)    # State update
            P = (1 - K) * P_pred             # Covariance update

            filtered[i] = x

        return filtered

    def smooth(
        self,
        latitudes: np.ndarray,
        longitudes: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Smooth ค่า latitude และ longitude พร้อมกัน

        Parameters
        ----------
        latitudes  : array ค่า latitude ดิบจาก GPS
        longitudes : array ค่า longitude ดิบจาก GPS

        Returns
        -------
        (smoothed_lat, smoothed_lng) : tuple ของ numpy array ที่ผ่าน filter แล้ว

        Example
        -------
        kf = KalmanFilter()
        lat_clean, lng_clean = kf.smooth(df["latitude"].values, df["longitude"].values)
        """
        smoothed_lat = self._filter_1d(np.array(latitudes, dtype=float))
        smoothed_lng = self._filter_1d(np.array(longitudes, dtype=float))
        return smoothed_lat, smoothed_lng
