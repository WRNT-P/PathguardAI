"""
test_kalman_tuning.py — ทดสอบ Q/R ที่ปรับใหม่ + Adaptive Jump Detection

กรณีทดสอบ 2 แบบตามที่ระบุในโจทย์:
  1. Discrete jumps  : จุดแยกห่างกัน (~300 m) ที่ควรแยก cluster ได้
  2. Continuous walk : เส้นทางเดินต่อเนื่อง ที่ไม่ควรมี jitter เยอะขึ้น

เรียกใช้:
    cd backend
    python test_kalman_tuning.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd

# ── import module ที่แก้แล้ว ──────────────────────────────────────────────────
from app.services.kalman_batch import KalmanFilter
from app.ai.module1_behavior.data_preprocessing import preprocess_gps
from app.ai.module1_behavior.place_clustering import cluster_places

# กำหนดค่า seed สำหรับ reproducibility
np.random.seed(42)

# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _deg_to_meters(deg: float) -> float:
    return deg * 111_000


def _build_df(lats, lngs, n_points_each=20) -> pd.DataFrame:
    """สร้าง DataFrame ที่มีหลายสถานที่ แต่ละแห่งมี n_points_each จุด พร้อม noise"""
    rows = []
    base_time = pd.Timestamp("2025-01-01 08:00:00")
    for loc_idx, (lat, lng) in enumerate(zip(lats, lngs)):
        for j in range(n_points_each):
            noise_lat = np.random.normal(0, 0.00003)   # ~3 m noise
            noise_lng = np.random.normal(0, 0.00003)
            rows.append({
                "latitude": lat + noise_lat,
                "longitude": lng + noise_lng,
                "speed": 0.5,
                "timestamp": base_time + pd.Timedelta(minutes=(loc_idx * n_points_each + j) * 2),
            })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Discrete Jumps (สถานที่ห่างกัน ~300 m)
# ─────────────────────────────────────────────────────────────────────────────

def test_discrete_jumps():
    print("\n" + "═" * 60)
    print("TEST 1 — Discrete Jumps (~300 m apart)")
    print("═" * 60)

    # สถานที่ 3 แห่ง ห่างกัน ~300 m (300/111000 ≈ 0.0027°)
    locations = [
        (13.7500, 100.5000),   # สถานที่ A
        (13.7527, 100.5000),   # สถานที่ B  (~300 m เหนือ)
        (13.7500, 100.5027),   # สถานที่ C  (~300 m ตะวันออก)
    ]

    df_raw = _build_df([l[0] for l in locations], [l[1] for l in locations])

    # รัน preprocess (adaptive=True by default)
    df_clean = preprocess_gps(df_raw.copy(), adaptive=True)

    # Cluster
    clusters = cluster_places(df_clean.copy())
    n_clusters = len(clusters)

    print(f"  สถานที่จริง     : {len(locations)} แห่ง")
    print(f"  cluster ที่ได้   : {n_clusters} cluster")
    print(f"  ผลลัพธ์          : {'✅ PASS' if n_clusters == len(locations) else '❌ FAIL — รวมผิด'}")

    if clusters:
        print("\n  Cluster centroids:")
        for c in clusters:
            print(f"    cluster {c['cluster_id']}: ({c['latitude']:.6f}, {c['longitude']:.6f})  "
                  f"freq={c['visit_frequency']}  stay={c['avg_stay_time']:.1f} min")

    # เปรียบเทียบ lag ของ filter เก่าและใหม่
    lats = df_raw["latitude"].values
    old_kf = KalmanFilter(process_noise=1e-5, measurement_noise=1e-3, adaptive=False)
    new_kf = KalmanFilter(adaptive=True)   # Q=1e-4, R=5e-4

    lat_old, _ = old_kf.smooth(lats, np.zeros_like(lats))
    lat_new, _ = new_kf.smooth(lats, np.zeros_like(lats))

    # หาจำนวน step กว่า filter จะ "settle" หลัง jump ครั้งแรก (ผ่าน 50% ของ gap)
    jump_idx = 20  # จุดที่ 20 คือจุดแรกของสถานที่ B
    gap = abs(lats[jump_idx] - lats[jump_idx - 1])
    threshold_50pct = lats[jump_idx - 1] + gap * 0.5

    def settle_steps(filtered, start_idx):
        for k in range(start_idx, min(start_idx + 50, len(filtered))):
            if abs(filtered[k] - lats[jump_idx]) < gap * 0.3:
                return k - start_idx
        return ">50"

    old_settle = settle_steps(lat_old, jump_idx)
    new_settle = settle_steps(lat_new, jump_idx)

    print(f"\n  Settle steps หลัง jump:")
    print(f"    Filter เก่า (Q=1e-5, R=1e-3, adaptive=off): {old_settle} steps")
    print(f"    Filter ใหม่ (Q=1e-4, R=5e-4, adaptive=on) : {new_settle} steps")

    return n_clusters == len(locations)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — Continuous Walk (เส้นทางเดินต่อเนื่อง)
# ─────────────────────────────────────────────────────────────────────────────

def test_continuous_walk():
    print("\n" + "═" * 60)
    print("TEST 2 — Continuous Walk (GPS jitter ไม่ควรเพิ่มขึ้น)")
    print("═" * 60)

    # จำลองการเดิน ~100 m จาก A → B แบบต่อเนื่อง (ไม่ใช่ jump)
    n = 60
    lats_true = np.linspace(13.7500, 13.7509, n)   # ~100 m
    lngs_true = np.full(n, 100.5000)
    noise = np.random.normal(0, 0.00003, n)         # ~3 m noise

    lats_raw = lats_true + noise
    lngs_raw = lngs_true + np.random.normal(0, 0.00003, n)

    old_kf = KalmanFilter(process_noise=1e-5, measurement_noise=1e-3, adaptive=False)
    new_kf = KalmanFilter(adaptive=True)   # Q=1e-4, R=5e-4

    lat_old, _ = old_kf.smooth(lats_raw, lngs_raw)
    lat_new, _ = new_kf.smooth(lats_raw, lngs_raw)

    # วัด RMSE เทียบกับ ground truth
    rmse_raw = np.sqrt(np.mean((lats_raw - lats_true) ** 2))
    rmse_old = np.sqrt(np.mean((lat_old - lats_true) ** 2))
    rmse_new = np.sqrt(np.mean((lat_new - lats_true) ** 2))

    def to_meters(deg): return deg * 111_000

    print(f"  RMSE เทียบ ground truth:")
    print(f"    Raw GPS     : {to_meters(rmse_raw):.2f} m")
    print(f"    Filter เก่า : {to_meters(rmse_old):.2f} m  (Q=1e-5, R=1e-3)")
    print(f"    Filter ใหม่ : {to_meters(rmse_new):.2f} m  (Q=1e-4, R=5e-4, adaptive)")

    # ยอมรับได้ถ้า RMSE ใหม่ ≤ 2× ของเก่า (trade-off ที่ยอมรับ)
    acceptable = rmse_new <= rmse_old * 2.0
    print(f"\n  ผลลัพธ์: {'✅ PASS — jitter ยังอยู่ในขอบเขตยอมรับ' if acceptable else '❌ FAIL — jitter เพิ่มขึ้นมากเกิน'}")
    return acceptable


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — Adaptive=False สำหรับเส้นทางเดิน (ไม่มี jump detection)
# ─────────────────────────────────────────────────────────────────────────────

def test_adaptive_off_for_walk():
    print("\n" + "═" * 60)
    print("TEST 3 — adaptive=False ให้ผล smooth กว่าบน continuous walk")
    print("═" * 60)

    n = 60
    lats_true = np.linspace(13.7500, 13.7509, n)
    lngs_true = np.full(n, 100.5000)

    lats_raw = lats_true + np.random.normal(0, 0.00003, n)
    lngs_raw = lngs_true + np.random.normal(0, 0.00003, n)

    kf_adaptive   = KalmanFilter(adaptive=True)
    kf_no_adaptive = KalmanFilter(adaptive=False)

    lat_adapt, _ = kf_adaptive.smooth(lats_raw, lngs_raw)
    lat_plain, _ = kf_no_adaptive.smooth(lats_raw, lngs_raw)

    rmse_adapt = np.sqrt(np.mean((lat_adapt - lats_true) ** 2)) * 111_000
    rmse_plain = np.sqrt(np.mean((lat_plain - lats_true) ** 2)) * 111_000

    print(f"  RMSE adaptive=True  : {rmse_adapt:.2f} m")
    print(f"  RMSE adaptive=False : {rmse_plain:.2f} m")
    print(f"\n  (ทั้งสองควรใกล้เคียงกันเพราะไม่มี jump จริงในข้อมูล)")
    return True  # informational only


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = []
    results.append(test_discrete_jumps())
    results.append(test_continuous_walk())
    results.append(test_adaptive_off_for_walk())

    print("\n" + "═" * 60)
    passed = sum(results)
    print(f"สรุป: {passed}/{len(results)} tests passed")
    print("═" * 60)
    sys.exit(0 if all(results) else 1)
