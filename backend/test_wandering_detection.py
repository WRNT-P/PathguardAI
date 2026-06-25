"""
test_wandering_detection.py — สคริปต์สำหรับทดสอบระบบ Wandering Detection (Module 2.3)

จำลองสถานการณ์การเดิน:
  1. การเดินแบบปกติ (Normal Walking) — เส้นทางตรง, ความเร็วคงที่
  2. การเดินวนเวียน/หลงทาง (Wandering) — เลี้ยวบ่อย (direction changes สูง), วนเวียนในรัศมีแคบ (displacement ratio สูง)
"""

import sys
import numpy as np
from app.ai.module2_prediction.wandering_detection import WanderingDetector

# ฟังก์ชันจำลองจุด GPS
def generate_straight_path(start_lat, start_lng, steps=30, step_distance_m=10.0, bearing_deg=0.0, add_noise=True):
    """จำลองการเดินเส้นทางตรง (Normal) โดยมี noise เล็กน้อย"""
    records = []
    lat = start_lat
    lng = start_lng
    
    lat_deg_per_m = 1.0 / 111000.0
    lng_deg_per_m = 1.0 / (111000.0 * np.cos(np.radians(start_lat)))
    
    current_bearing = bearing_deg
    # ล็อค seed เพื่อให้สร้างข้อมูลเหมือนเดิมทุกครั้ง
    np.random.seed(42)
    
    for i in range(steps):
        if add_noise:
            # ความเร็วมีสั่นไหวเล็กน้อย ~1.1 ถึง 1.7 m/s
            speed = float(max(0.8, 1.4 + np.random.normal(0, 0.15)))
            # ทิศทางเบี่ยงเบนเล็กน้อยทีละนิด
            current_bearing += float(np.random.normal(0, 2.0))
        else:
            speed = 1.4
            
        rad = np.radians(current_bearing)
        d_lat = step_distance_m * np.cos(rad) * lat_deg_per_m
        d_lng = step_distance_m * np.sin(rad) * lng_deg_per_m
        
        records.append({
            "latitude": lat,
            "longitude": lng,
            "speed": speed,
        })
        lat += d_lat
        lng += d_lng
        
    return records

def generate_curved_path(start_lat, start_lng, steps=30, step_distance_m=10.0, start_bearing=0.0, turn_rate=5.0):
    """จำลองการเดินโค้งปกติ (Normal Curve)"""
    records = []
    lat = start_lat
    lng = start_lng
    
    lat_deg_per_m = 1.0 / 111000.0
    lng_deg_per_m = 1.0 / (111000.0 * np.cos(np.radians(start_lat)))
    
    current_bearing = start_bearing
    np.random.seed(42 + int(abs(start_bearing)))
    for i in range(steps):
        speed = float(max(0.8, 1.3 + np.random.normal(0, 0.1)))
        current_bearing += turn_rate + float(np.random.normal(0, 1.0))
        
        rad = np.radians(current_bearing)
        d_lat = step_distance_m * np.cos(rad) * lat_deg_per_m
        d_lng = step_distance_m * np.sin(rad) * lng_deg_per_m
        
        records.append({
            "latitude": lat,
            "longitude": lng,
            "speed": speed,
        })
        lat += d_lat
        lng += d_lng
        
    return records

def generate_wandering_path(start_lat, start_lng, steps=30):
    """จำลองการเดินวนเวียนในบริเวณแคบๆ (Wandering)"""
    records = []
    lat = start_lat
    lng = start_lng
    
    lat_deg_per_m = 1.0 / 111000.0
    lng_deg_per_m = 1.0 / (111000.0 * np.cos(np.radians(start_lat)))
    
    # เดินวนเป็นวงกลม/หยักไปหยักมา
    np.random.seed(42)
    for i in range(steps):
        angle = (i * 45) % 360  # เลี้ยวบ่อยๆ ทุก 45 องศา
        rad = np.radians(angle + np.random.normal(0, 5))
        dist = 5.0 + np.random.normal(0, 1) # ก้าวทีละ 5 เมตร
        
        lat += dist * np.cos(rad) * lat_deg_per_m
        lng += dist * np.sin(rad) * lng_deg_per_m
        
        records.append({
            "latitude": lat,
            "longitude": lng,
            "speed": 0.8,  # เดินช้าลง
        })
    return records

def run_tests():
    print("=" * 60)
    print(" เริ่มการทดสอบ Wandering Detection (Module 2.3)")
    print("=" * 60)

    # 1. ทดสอบการจำลองข้อมูล
    normal_history = []
    # จำลองประวัติการเดินปกติที่หลากหลาย (ตรง, โค้ง, ช้า, เร็ว) เพื่อให้โมเดลมี Variance
    # ตรง 4 ทิศทางหลัก
    for b in [0, 90, 180, 270]:
        normal_history.extend(generate_straight_path(13.75, 100.50, steps=30, bearing_deg=b))
    # ตรงแบบก้าวสั้น (ช้า) และก้าวยาว (เร็ว)
    normal_history.extend(generate_straight_path(13.75, 100.50, steps=30, bearing_deg=45, step_distance_m=5.0))
    normal_history.extend(generate_straight_path(13.75, 100.50, steps=30, bearing_deg=135, step_distance_m=15.0))
    # โค้งปกติ
    normal_history.extend(generate_curved_path(13.75, 100.50, steps=30, start_bearing=0.0, turn_rate=3.0))
    normal_history.extend(generate_curved_path(13.75, 100.50, steps=30, start_bearing=180.0, turn_rate=-3.0))
        
    print(f"จำลองประวัติการเดินปกติเรียบร้อย: {len(normal_history)} จุด")

    # 2. ทดสอบการ Fit model
    detector = WanderingDetector(contamination=0.05, window_size=15)
    fit_res = detector.fit(normal_history)
    print(f"ผลลัพธ์การ Fit Model: {fit_res}")
    
    if fit_res.get("status") != "fitted":
        print("ข้อผิดพลาด: ไม่สามารถ fit model ได้!")
        sys.exit(1)

    # 3. ทดสอบ Detect การเดินปกติ (ควรจะมี wandering score ต่ำ และไม่เตือน)
    print("\n" + "-" * 50)
    print("TEST 1: เดินตรงปกติ (Normal Walk)")
    print("-" * 50)
    
    normal_test = generate_straight_path(13.75, 100.50, steps=20, bearing_deg=10)
    res_normal = detector.detect(normal_test)
    
    print(f"Wandering Score  : {res_normal['wandering_score']} ({res_normal['wandering_level']})")
    print(f"Detected         : {res_normal['wandering_detected']}")
    print(f"Features สกัดได้  : {res_normal['features']}")
    
    assert res_normal['wandering_level'] == 'normal', "การเดินปกติไม่ควรตรวจเจอเป็นหลงทาง"

    # 4. ทดสอบ Detect การเดินวนเวียน/หลงทาง (ควรจะมี wandering score สูง)
    print("\n" + "-" * 50)
    print("TEST 2: เดินวนเวียนหลงทาง (Wandering Walk)")
    print("-" * 50)
    
    wandering_test = generate_wandering_path(13.75, 100.50, steps=20)
    res_wandering = detector.detect(wandering_test)
    
    print(f"Wandering Score  : {res_wandering['wandering_score']} ({res_wandering['wandering_level']})")
    print(f"Detected         : {res_wandering['wandering_detected']}")
    print(f"Features สกัดได้  : {res_wandering['features']}")
    
    assert res_wandering['wandering_detected'] is True, "ควรตรวจจับการเดินวนเวียนหลงทางได้"

    # 5. ทดสอบ Rule-based Fallback (จำลองแบบไม่ fit model)
    print("\n" + "-" * 50)
    print("TEST 3: Rule-based Fallback (ไม่ได้ fit model)")
    print("-" * 50)
    
    detector_unfitted = WanderingDetector(window_size=15)
    # ส่งข้อมูลเดินวนเข้าตรวจจับทันทีโดยไม่ได้ fit
    res_fallback = detector_unfitted.detect(wandering_test)
    
    print(f"Wandering Score (Rule-based) : {res_fallback['wandering_score']} ({res_fallback['wandering_level']})")
    print(f"Detected (Rule-based)        : {res_fallback['wandering_detected']}")
    print(f"Features สกัดได้              : {res_fallback['features']}")
    
    assert res_fallback['wandering_detected'] is True, "Rule-based ควรรองรับการตรวจจับได้เบื้องต้น"

    print("\n" + "=" * 60)
    print(" ผลทดสอบทั้งหมดเสร็จสมบูรณ์และถูกต้อง! ✅")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
