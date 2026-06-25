"""
test_stop_confusion.py — สคริปต์สำหรับทดสอบระบบ Stop vs Confusion Classification (Module 2.4)
"""

import sys
import numpy as np
from app.ai.module2_prediction.stop_confusion_classification import StopConfusionClassifier

# จำลองจุด GPS ประวัติก่อนหยุดเดิน
def generate_gps_history(start_lat, start_lng, steps=10, is_curvy=False):
    records = []
    lat, lng = start_lat, start_lng
    lat_deg = 1.0 / 111000.0
    lng_deg = 1.0 / (111000.0 * np.cos(np.radians(start_lat)))
    
    np.random.seed(42)
    current_bearing = 0.0
    for i in range(steps):
        if is_curvy:
            # เลี้ยวบ่อยสลับทิศทาง
            current_bearing = (i * 90) % 360
        else:
            # เดินเกือบตรง
            current_bearing = 10.0
            
        rad = np.radians(current_bearing)
        lat += 10.0 * np.cos(rad) * lat_deg
        lng += 10.0 * np.sin(rad) * lng_deg
        
        records.append({
            "latitude": lat,
            "longitude": lng,
            "speed": 0.5 if is_curvy else 1.4  # เดินช้าลงถ้าเลี้ยวบ่อยสับสน
        })
    return records

def run_tests():
    print("=" * 60)
    print(" เริ่มการทดสอบ Stop vs Confusion Classification (Module 2.4)")
    print("=" * 60)

    # ข้อมูลสถานที่คุ้นเคยในฐานข้อมูล
    known_places = [
        {"latitude": 13.7500, "longitude": 100.5000, "label": "home"},
        {"latitude": 13.7550, "longitude": 100.5050, "label": "market"}
    ]
    
    # เส้นทางที่คาดหมายไว้จาก RoutePredictor
    predicted_route = [
        (13.7500, 100.5000),
        (13.7510, 100.5010),
        (13.7520, 100.5020),
        (13.7530, 100.5030)
    ]

    # สร้างลักษณนาม Classifier
    clf = StopConfusionClassifier()

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 1: Rule-based Fallback (ไม่ได้ฝึกโมเดล)
    # ─────────────────────────────────────────────────────────────────────────
    print("\nTEST 1 — Rule-based Fallback")
    print("-" * 50)
    
    # สัญญานสับสน: หยุดนิ่งกลางทาง 10 นาที (600s), นอกเส้นทางห่าง 300m, เลี้ยวไปมาก่อนหยุด, ไม่คุ้นที่
    confused_gps = generate_gps_history(13.7800, 100.5500, steps=10, is_curvy=True)
    res_fallback = clf.classify(
        recent_gps=confused_gps,
        stop_duration_seconds=600,
        current_lat=13.7800,
        current_lng=100.5500,
        predicted_route=predicted_route,
        known_places=known_places
    )
    
    print(f"Fallback Status : {res_fallback['status']}")
    print(f"Fallback Conf   : {res_fallback['confidence_score']}")
    print(f"Fallback Feats  : {res_fallback['features']}")
    
    assert res_fallback['status'] == "confused", "การเดินนอกเส้นทาง นาน และเลี้ยวบ่อย ควรเป็น confused"

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 2: Fit model ด้วยข้อมูลจำลอง (Synthetic Train)
    # ─────────────────────────────────────────────────────────────────────────
    print("\nTEST 2 — Training model on synthetic data")
    print("-" * 50)
    
    fit_res = clf.fit_synthetic()
    print(f"Fit Result      : {fit_res}")
    assert fit_res['status'] == "trained", "ควรฟิตโมเดลสำเร็จ"

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 3: Classification — Normal Stop (หยุดซื้อของใกล้บ้าน/ใกล้ตลาด)
    # ─────────────────────────────────────────────────────────────────────────
    print("\nTEST 3 — Predict: Normal Stop")
    print("-" * 50)
    
    # เดินตรงปกติ ความเร็ว 1.4m/s, หยุดสั้น 1 นาที (60s), อยู่ใกล้ตลาดพอดี (ห่างตลาด ~10 เมตร)
    normal_gps = generate_gps_history(13.7551, 100.5051, steps=10, is_curvy=False)
    res_normal = clf.classify(
        recent_gps=normal_gps,
        stop_duration_seconds=60,
        current_lat=13.7551,
        current_lng=100.5051,
        predicted_route=predicted_route,
        known_places=known_places
    )
    
    print(f"Normal Status   : {res_normal['status']}")
    print(f"Normal Conf     : {res_normal['confidence_score']}")
    print(f"Normal Feats    : {res_normal['features']}")
    
    assert res_normal['status'] == "normal", "หยุดสั้น ใกล้สถานที่คุ้นเคย ควรจำแนกเป็น normal"

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 4: Classification — Confused Stop (หยุดสับสนกลางทางไกลบ้าน)
    # ─────────────────────────────────────────────────────────────────────────
    print("\nTEST 4 — Predict: Confused Stop")
    print("-" * 50)
    
    # เดินสับสนเลี้ยวไปมา, หยุดนิ่งนาน 15 นาที (900s), อยู่ไกลจากสถานที่คุ้นเคย, นอกเส้นทางที่คาดเดา
    confused_gps_real = generate_gps_history(13.7900, 100.5900, steps=10, is_curvy=True)
    res_confused = clf.classify(
        recent_gps=confused_gps_real,
        stop_duration_seconds=900,
        current_lat=13.7900,
        current_lng=100.5900,
        predicted_route=predicted_route,
        known_places=known_places
    )
    
    print(f"Confused Status : {res_confused['status']}")
    print(f"Confused Conf   : {res_confused['confidence_score']}")
    print(f"Confused Feats  : {res_confused['features']}")
    
    assert res_confused['status'] == "confused", "หยุดนานในที่ไม่คุ้นเคยและนอกเส้นทางควรจำแนกเป็น confused"

    print("\n" + "=" * 60)
    print(" ผลทดสอบ Stop Confusion Classifier สำเร็จลุล่วงและถูกต้อง! ✅")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
