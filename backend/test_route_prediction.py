"""
test_route_prediction.py — ทดสอบ Module 2.2 RoutePredictor

Test cases:
  1. fit()          — สร้าง transition matrix ถูกต้องจาก synthetic GPS history
  2. predict_route() — คืน waypoints ที่ผ่าน cluster กลาง (A → B → C)
  3. similarity     — score สูงเมื่อเส้นทางเหมือนเดิม, ต่ำเมื่อต่างกัน
  4. edge cases     — กรณี current == destination, ไม่มี history

เรียกใช้:
    cd backend && python3 test_route_prediction.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta
import numpy as np

from app.ai.module2_prediction.route_prediction import RoutePredictor

np.random.seed(42)
PASS = "✅ PASS"
FAIL = "❌ FAIL"


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic data helpers
# ─────────────────────────────────────────────────────────────────────────────

# สถานที่ 3 แห่ง: บ้าน (0) → ร้านกาแฟ (1) → ที่ทำงาน (2)
# ห่างกัน ~300 m ตามแกนละติจูด
KNOWN_PLACES = [
    {"cluster_id": 0, "latitude": 13.7500, "longitude": 100.5000},   # บ้าน
    {"cluster_id": 1, "latitude": 13.7527, "longitude": 100.5000},   # ร้านกาแฟ (~300 m)
    {"cluster_id": 2, "latitude": 13.7554, "longitude": 100.5000},   # ที่ทำงาน (~600 m)
]

BASE_TIME = datetime(2025, 1, 1, 8, 0, 0)


def _make_gps(lat, lng, t: datetime, noise_m: float = 5.0) -> dict:
    """GPS record ปลอมพร้อม noise เล็กน้อย (meters → degrees)."""
    noise_deg = noise_m / 111_000
    return {
        "latitude":    lat + np.random.normal(0, noise_deg),
        "longitude":   lng + np.random.normal(0, noise_deg),
        "recorded_at": t,
    }


def _make_route(path: list[int], points_per_stop: int = 15,
                start_time: datetime = BASE_TIME) -> list[dict]:
    """สร้าง GPS records ของ 1 วันที่ผ่าน cluster sequence ที่กำหนด."""
    records = []
    t = start_time
    for cid in path:
        p = KNOWN_PLACES[cid]
        for _ in range(points_per_stop):
            records.append(_make_gps(p["latitude"], p["longitude"], t))
            t += timedelta(minutes=2)
    return records


def _build_history(n_days: int = 20) -> list[dict]:
    """สร้าง GPS history 20 วัน: ทุกวัน 0 → 1 → 2."""
    all_records = []
    for day in range(n_days):
        start = BASE_TIME + timedelta(days=day)
        all_records += _make_route([0, 1, 2], start_time=start)
    return all_records


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — fit() สร้าง transition matrix
# ─────────────────────────────────────────────────────────────────────────────

def test_fit():
    print("\n" + "═" * 60)
    print("TEST 1 — fit() สร้าง Transition Matrix")
    print("═" * 60)

    history = _build_history(n_days=20)
    rp = RoutePredictor()
    result = rp.fit(history, KNOWN_PLACES)

    print(f"  Status             : {result['status']}")
    print(f"  n_clusters         : {result['n_clusters']}")
    print(f"  n_transitions      : {result['n_transitions']}")
    print(f"  n_historical_routes: {result['n_historical_routes']}")

    # Transition matrix ต้องมี A[0,1] และ A[1,2] สูงที่สุด (เส้นทาง 0→1→2)
    A = rp.transition_matrix
    print(f"\n  Transition Matrix (row=from, col=to):")
    for i in range(3):
        row = "  ".join(f"{A[i, j]:.2f}" for j in range(3))
        print(f"    cluster {i}: [{row}]")

    ok = (
        result["status"] == "fitted" and
        result["n_clusters"] == 3 and
        result["n_historical_routes"] >= 1 and
        A[0, 1] > 0.5 and   # บ้าน → ร้านกาแฟ บ่อยที่สุด
        A[1, 2] > 0.5       # ร้านกาแฟ → ทำงาน บ่อยที่สุด
    )
    print(f"\n  ผลลัพธ์: {PASS if ok else FAIL}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — predict_route() คืน waypoints ที่ถูกต้อง (0 → 1 → 2)
# ─────────────────────────────────────────────────────────────────────────────

def test_predict_route_waypoints():
    print("\n" + "═" * 60)
    print("TEST 2 — predict_route() คืน waypoints ผ่าน cluster กลาง")
    print("═" * 60)

    history = _build_history(n_days=20)
    rp = RoutePredictor()
    rp.fit(history, KNOWN_PLACES)

    # อยู่ที่บ้าน (cluster 0) → ไปที่ทำงาน (cluster 2)
    recent = _make_route([0], points_per_stop=10)  # GPS 10 จุดที่บ้าน
    result = rp.predict_route(
        recent_gps=recent,
        destination_cluster_id=2,
        known_places=KNOWN_PLACES,
    )

    print(f"  Status         : {result['status']}")
    print(f"  waypoint_count : {result['waypoint_count']}")
    print(f"  Waypoints:")
    for w in result["predicted_route"]:
        print(f"    cluster {w['cluster_id']}: ({w['latitude']:.6f}, {w['longitude']:.6f})")

    route_cluster_ids = [w["cluster_id"] for w in result["predicted_route"]]
    ok = (
        result["status"] == "ok" and
        route_cluster_ids[0] == 0 and       # เริ่มที่บ้าน
        route_cluster_ids[-1] == 2 and      # จบที่ทำงาน
        1 in route_cluster_ids              # ผ่านร้านกาแฟ
    )
    print(f"\n  cluster path: {route_cluster_ids}")
    print(f"  ผลลัพธ์: {PASS if ok else FAIL}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — similarity_score สูงสำหรับเส้นทางเดิม, ต่ำสำหรับเส้นทางใหม่
# ─────────────────────────────────────────────────────────────────────────────

def test_similarity_score():
    print("\n" + "═" * 60)
    print("TEST 3 — similarity_score")
    print("═" * 60)

    # History เฉพาะเส้นทาง 0→1→2 (ไม่มีเส้นทางย้อนกลับ)
    history = _build_history(n_days=20)
    rp = RoutePredictor()
    rp.fit(history, KNOWN_PLACES)

    # เส้นทางที่มีใน history: อยู่บ้าน (0) → ไปทำงาน (2) — similarity สูง
    recent_familiar = _make_route([0], points_per_stop=10)
    result_familiar = rp.predict_route(
        recent_gps=recent_familiar,
        destination_cluster_id=2,
        known_places=KNOWN_PLACES,
    )

    # เส้นทางที่ไม่มีใน history: destination=1 (ร้านกาแฟ) จาก cluster 2
    # history มีแต่ 0→1→2 ไม่มีใครจบที่ cluster 1 → similarity=0.0
    recent_novel = _make_route([2], points_per_stop=10)
    result_novel = rp.predict_route(
        recent_gps=recent_novel,
        destination_cluster_id=1,   # destination ที่ไม่มีใครเคยไปจบที่นี่
        known_places=KNOWN_PLACES,
    )

    sim_familiar = result_familiar["similarity_score"]
    sim_novel    = result_novel["similarity_score"]

    print(f"  เส้นทางที่มีใน history (0→..→2) : similarity = {sim_familiar:.3f}  "
          f"familiar={result_familiar['route_familiar']}")
    print(f"  destination ไม่มีใน history (→1) : similarity = {sim_novel:.3f}  "
          f"familiar={result_novel['route_familiar']}")

    ok = (
        sim_familiar > sim_novel and     # เส้นทางที่คุ้นเคยควร score สูงกว่า
        result_familiar["route_familiar"] is True and
        result_novel["similarity_score"] == 0.0   # ไม่มี history ไปถึง dest=1
    )
    print(f"\n  ผลลัพธ์: {PASS if ok else FAIL}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Edge case: current == destination
# ─────────────────────────────────────────────────────────────────────────────

def test_already_at_destination():
    print("\n" + "═" * 60)
    print("TEST 4 — Edge case: destination อยู่ในเส้นทางเสมอ")
    print("═" * 60)

    history = _build_history(n_days=5)
    rp = RoutePredictor()
    rp.fit(history, KNOWN_PLACES)

    # อยู่ที่บ้าน (cluster 0) → ไปทำงาน (cluster 2)
    # ตรวจสอบว่า predicted_route จบด้วย cluster 2 เสมอ
    recent = _make_route([0], points_per_stop=10)
    result = rp.predict_route(
        recent_gps=recent,
        destination_cluster_id=2,
        known_places=KNOWN_PLACES,
    )

    last_cluster = result["predicted_route"][-1]["cluster_id"]
    print(f"  status          : {result['status']}")
    print(f"  waypoint_count  : {result['waypoint_count']}")
    print(f"  last waypoint   : cluster {last_cluster}  (ต้องเป็น 2)")
    print(f"  similarity_score: {result['similarity_score']}")

    ok = (
        result["status"] == "ok" and
        last_cluster == 2 and           # จบที่ destination เสมอ
        result["waypoint_count"] >= 1
    )
    print(f"\n  ผลลัพธ์: {PASS if ok else FAIL}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — Edge case: ยังไม่ได้ fit
# ─────────────────────────────────────────────────────────────────────────────

def test_not_fitted():
    print("\n" + "═" * 60)
    print("TEST 5 — Edge case: เรียก predict_route โดยไม่ fit ก่อน")
    print("═" * 60)

    rp = RoutePredictor()
    result = rp.predict_route(
        recent_gps=[{"latitude": 13.75, "longitude": 100.5}],
        destination_cluster_id=2,
        known_places=KNOWN_PLACES,
    )

    print(f"  status: {result['status']}")
    ok = result["status"] == "not_fitted"
    print(f"  ผลลัพธ์: {PASS if ok else FAIL}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = [
        test_fit(),
        test_predict_route_waypoints(),
        test_similarity_score(),
        test_already_at_destination(),
        test_not_fitted(),
    ]

    print("\n" + "═" * 60)
    passed = sum(results)
    print(f"สรุป: {passed}/{len(results)} tests passed")
    print("═" * 60)
    sys.exit(0 if all(results) else 1)
