# pathguard/backend/app/services/kalman_batch.py
#
# Batch Kalman Filter สำหรับลด GPS Noise ของข้อมูลย้อนหลังทั้งก้อน (เช่น 30 วัน)
# ใช้ 1D Kalman Filter แยกกันสำหรับ latitude และ longitude
#
# หมายเหตุ: สำหรับ smoothing GPS แบบ "สด" ทีละจุด ให้ใช้ app.services.kalman_filter
#           ไฟล์นี้ใช้กับ AI module 1 (data_preprocessing) ที่ประมวลผลเป็น DataFrame
#
# หลักการ:
#   - State          : ค่า GPS ที่ "แท้จริง" ที่เราประมาณ
#   - Process noise (Q) : ความไม่แน่นอนของการเคลื่อนที่  (ยิ่งสูง → ตามตำแหน่งจริงได้เร็ว)
#   - Measurement noise (R) : ความไม่แน่นอนของ GPS sensor  (ยิ่งสูง → smooth มาก)
#   - P              : ค่าความไม่แน่นอนของ state ปัจจุบัน
#
# ────────────────────────────────────────────────────────────────────
#  การปรับ Q/R ต้องคำนึง trade-off:
#
#   Q สูง / R ต่ำ  → ตาม GPS จริงเร็ว  แต่ jitter เข้ามาด้วย
#   Q ต่ำ / R สูง  → smooth มาก       แต่ lag เยอะ → cluster ผิด
#
#  ค่าเดิม:  Q=1e-5, R=1e-3  →  Gain ≈ 0.05  →  lag ~18 จุด
#  ค่าใหม่:  Q=1e-4, R=5e-4  →  Gain ≈ 0.17  →  lag ~6 จุด
#
#  นอกจากนั้นเพิ่ม Adaptive Jump Detection:
#   ถ้าจุดกระโดด > jump_threshold_deg (ประมาณ 100 m โดย default)
#   → รีเซ็ต P = jump_reset_P เพื่อให้ Gain พุ่งขึ้น → filter เชื่อค่าใหม่ทันที
# ────────────────────────────────────────────────────────────────────

import numpy as np

# 1 degree ≈ 111 km  →  100 m ≈ 0.0009 degree
_100M_IN_DEG = 100 / 111_000  # ≈ 0.0009°


class KalmanFilter:
    """
    1D Kalman Filter สำหรับ smooth ข้อมูล GPS (lat/lng)

    Parameters
    ----------
    process_noise (Q)      : ยิ่งสูง → ยืดหยุ่นกับการเปลี่ยนแปลงมาก (ตามการเคลื่อนที่เร็ว)
                             ค่า default ใหม่ 1e-4 (เดิม 1e-5) เพื่อลด lag
    measurement_noise (R)  : ยิ่งสูง → ไม่ค่อยเชื่อ sensor (smooth มากขึ้น)
                             ค่า default ใหม่ 5e-4 (เดิม 1e-3) เพื่อเพิ่ม Gain เล็กน้อย
    adaptive               : ถ้า True จะใช้ Jump Detection
                             รีเซ็ต P ทุกครั้งที่ตำแหน่งกระโดดเกิน jump_threshold_deg
    jump_threshold_deg     : ระยะกระโดดขั้นต่ำ (degree) ที่ถือว่าเป็นการย้ายสถานที่จริง
                             default ≈ 100 m
    jump_reset_P           : ค่า P ที่รีเซ็ตเป็นเมื่อเจอ jump (สูง → Gain สูง → เชื่อค่าใหม่เร็ว)
    """

    def __init__(
        self,
        process_noise: float = 1e-4,       # ⬆ จากเดิม 1e-5
        measurement_noise: float = 5e-4,   # ⬇ จากเดิม 1e-3
        adaptive: bool = True,
        jump_threshold_deg: float = _100M_IN_DEG,
        jump_reset_P: float = 1.0,
    ):
        self.Q = process_noise
        self.R = measurement_noise
        self.adaptive = adaptive
        self.jump_threshold_deg = jump_threshold_deg
        self.jump_reset_P = jump_reset_P

    def _filter_1d(self, measurements: np.ndarray) -> np.ndarray:
        """
        รัน Kalman Filter บน array 1 มิติ

        ถ้า adaptive=True จะ detect jump แล้วรีเซ็ต P เพื่อให้
        filter ตามตำแหน่งใหม่ทันที แทนที่จะ lag นานหลายสิบจุด
        """
        n = len(measurements)
        filtered = np.zeros(n)

        # Initial state
        x = measurements[0]   # State estimate (เริ่มจากค่าแรก)
        P = 1.0               # Initial uncertainty (สูง → Gain สูง → เชื่อ measurement แรก)

        for i, z in enumerate(measurements):
            # ── Adaptive: ตรวจจับการกระโดดตำแหน่ง ────────────────────────
            if self.adaptive and i > 0:
                jump = abs(z - x)
                if jump > self.jump_threshold_deg:
                    # รีเซ็ต P → Kalman Gain พุ่งขึ้น → filter เชื่อ measurement ใหม่ทันที
                    P = self.jump_reset_P

            # ── Predict ──────────────────────────────────────────────────
            x_pred = x
            P_pred = P + self.Q

            # ── Update ───────────────────────────────────────────────────
            K = P_pred / (P_pred + self.R)   # Kalman Gain  (0 = เชื่อ model, 1 = เชื่อ sensor)
            x = x_pred + K * (z - x_pred)   # State update
            P = (1 - K) * P_pred            # Covariance update

            filtered[i] = x

        return filtered

    def smooth(
        self,
        latitudes: np.ndarray,
        longitudes: np.ndarray,
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

        # ปิด adaptive สำหรับ GPS เส้นทางเดินต่อเนื่อง (ไม่ต้องการ jump detection)
        kf_walk = KalmanFilter(adaptive=False)
        lat_walk, lng_walk = kf_walk.smooth(df["latitude"].values, df["longitude"].values)
        """
        smoothed_lat = self._filter_1d(np.array(latitudes, dtype=float))
        smoothed_lng = self._filter_1d(np.array(longitudes, dtype=float))
        return smoothed_lat, smoothed_lng
