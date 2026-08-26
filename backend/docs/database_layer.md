# PathGuard AI — ชั้นฐานข้อมูล (Database Layer)

อ้างอิงจาก `app/db/models.py` (ORM/ตาราง) และ `app/db/crud.py` (ฟังก์ชันอ่าน/เขียน) จริง
ระบบใช้ **PostgreSQL** เก็บประวัติ/ผล AI ทั้งหมด ส่วน **Firebase Realtime DB** เก็บตำแหน่งสด (live) แยกต่างหาก ไม่อยู่ใน ORM นี้

> **นโยบาย transaction:** ฟังก์ชันใน `crud.py` ทำแค่ `flush` (กำหนด PK) แต่ **ไม่ commit** — ตัวที่ commit คือ dependency `get_db` ของ FastAPI เมื่อจบ request

> อัปเดตล่าสุด: **2026-08-22** — เพิ่ม 2 ตารางใหม่ และแก้ข้อสังเกตท้ายไฟล์ที่ล้าสมัย
> (สถานะงานตัวจริงอยู่ที่ `CLAUDE.md` + `docs/plan_person2_lane.md`)

---

## 1. แต่ละตารางเก็บอะไร (ORM models)

### `users` (model: `User`)
ข้อมูลผู้ใช้ทั้งผู้ป่วยและผู้ดูแล เป็น "แม่กุญแจ" ที่ทุกตารางอื่นอ้างถึงผ่าน FK `patient_id`
- คอลัมน์หลัก: `id` (PK, int ภายใน), `firebase_uid` (string จาก Firebase, unique), `name`, `role` (`patient`/`caregiver`), `caregiver_id` (FK ชี้ผู้ดูแลในตารางเดียวกัน), `created_at`
- ความสัมพันธ์: 1 user → หลาย `gps_records`, `risk_scores`, `alerts`, `behavioral_profiles` (ลบ user แล้ว cascade ลบหมด)

### `gps_data` (model: `GPSData`)
ประวัติ GPS ย้อนหลัง (≈30 วัน) ของผู้ป่วย — เก็บทั้งค่าดิบและค่าที่ผ่าน Kalman แล้ว
- คอลัมน์หลัก: `patient_id` (FK), `latitude`/`longitude` (ดิบ), `smooth_latitude`/`smooth_longitude` (Kalman), `accuracy` (m), `speed` (m/s), `altitude` (m), `direction` (องศา 0–359), `device_motion` (เช่น `walking`/`still`), `synthetic_injected` (bool — `True`=จุดที่ inject สังเคราะห์เพื่อ demo/ทดสอบ, `False`=ข้อมูลจริง), `recorded_at` (เวลาที่วัดจริง), `created_at`
- หมายเหตุ: ตำแหน่ง **สด** ไม่ได้เก็บที่นี่ — ไปอยู่ Firebase ; ตารางนี้คือ "ประวัติ" ที่ AI ใช้เรียนรู้

### `risk_scores` (model: `RiskScore`)
ผลคะแนนความเสี่ยงที่ Module 3 คำนวณในแต่ละครั้ง (เก็บเป็นประวัติ ไม่ทับของเก่า)
- คอลัมน์หลัก: `patient_id` (FK), `score` (0–100), `level` (`low`/`medium`/`high`), `wandering_detected` (bool), `gps_available` (bool), `factors` (JSON string ของ contributions แต่ละปัจจัย), `calculated_at`

### `alerts` (model: `Alert`)
การแจ้งเตือนที่เกิดขึ้น (ฉุกเฉิน/เข้าเขตอันตราย/สัญญาณ GPS หาย/หลงทาง)
- คอลัมน์หลัก: `patient_id` (FK), `alert_type` (`wandering`/`geofence`/`gps_loss`/`emergency`), `severity` (`low`/`medium`/`high`/`critical`), `message`, `latitude`/`longitude` (ตำแหน่งตอนเตือน), `resolved` (bool), `resolved_at`, `created_at`

### `behavioral_profiles` (model: `BehavioralProfile`)
โปรไฟล์พฤติกรรมที่ Module 1 เรียนรู้ — **หนึ่งแถวต่อหนึ่งผู้ป่วย** (`patient_id` unique) เป็นจุดเชื่อมกลางที่ Module 2/3/5 อ่านไปใช้
- คอลัมน์หลัก: `patient_id` (FK, unique), `known_places` (JSON: รายการสถานที่คุ้นเคย `{cluster_id, lat, lng, visit_frequency, avg_stay_time}`), `routine_patterns` (JSON กิจวัตรรายชั่วโมง — ปัจจุบันยังไม่ถูกตั้งค่า), `typical_range_km`, `last_trained_at`, `updated_at` (auto onupdate)

---

## 2. ใครเขียน / ใครอ่าน แต่ละตาราง

| ตาราง | เก็บอะไร | ใครเขียน | ใครอ่าน |
|---|---|---|---|
| `users` | ผู้ป่วย/ผู้ดูแล + FK กลาง | `api/users.py` (`POST /api/register` → `create_user`) ; `seed_module5.py` | `api/users.py` + `api/gps.py` (resolve `firebase_uid`→`id` ผ่าน `get_user_id_by_firebase_uid`) ; `seed_module5.py` |
| `gps_data` | ประวัติ GPS ดิบ+smooth | **Module 1** `services/gps_processor.py` (`save_gps_point`) ; `scripts/import_geolife.py` (bulk-load ข้อมูลจริง GeoLife) ; `scripts/inject_wandering.py` (จุด pacing สังเคราะห์, `synthetic_injected=True`) | **Module 1** `behavior_pipeline.py` ; **Module 2** `destination_prediction.py` ; **Module 3** `api/risk.py` ; **Module 4** `api/search_area.py` — ทั้งหมดผ่าน `get_gps_history` ; `get_latest_gps` อ่านโดย **Module 3** (`api/risk.py`) + **Module 4** (`api/search_area.py`) + **Module 5** (`api/recommendation.py`) |
| `behavioral_profiles` | สถานที่/กิจวัตรที่เรียนรู้ | **Module 1** `behavior_pipeline.py` (`upsert_behavioral_profile`) ; `seed_module5.py` | **Module 2** `destination_prediction.py` ; **Module 3** `api/risk.py` ; **Module 4** `api/search_area.py` ; **Module 5** `api/recommendation.py` — ทั้งหมดผ่าน `get_behavioral_profile` |
| `risk_scores` | ผลคะแนนเสี่ยง 0–100 | **Module 3** `api/risk.py` (`save_risk_score`) — เขียนเองทุกครั้งที่ GPS เข้า (throttle 60 วิ) | `api/gps.py` อ่าน `calculated_at` เป็นตัวจับเวลา throttle ; `get_recent_risk_scores` ใช้โดยกฎ temporal (trend/sustained) |
| `alerts` | การแจ้งเตือน | **Module 3** `api/risk.py` (`save_alert` — emergency + gps_loss) ; **Module 4** `api/search_area.py` (`save_alert` — gps_loss) | **`api/alerts.py`** (`GET /api/patients/{id}/alerts`, `PATCH /api/alerts/{id}`) ตั้งแต่ 22 ส.ค. ; `services/notification.py` อ่านเพื่อยิง push |
| `device_tokens` | FCM token ของเครื่องผู้ดูแล (22 ส.ค.) | `api/devices.py` (`POST /api/devices/token`) | `services/notification.py` |
| `push_notifications` | ประวัติ push ที่ส่งจริง + **state ของ cooldown** (22 ส.ค.) | `services/notification.py` | `services/notification.py` (เช็คว่าเพิ่งส่งไปหรือยัง) |

> หมายเหตุ `get_user_id_by_firebase_uid` ใช้แปลง `firebase_uid` (string) → `users.id` (int) ก่อนเขียน GPS เพราะ `gps_data.patient_id` เป็น FK แบบ int

---

## 3. ข้อมูลไหลและคงอยู่อย่างไร (Persistence flow)

```
[1] ลงทะเบียน
   Flutter → POST /api/register → users (สร้าง users.id) ──┐
                                                           │ FK patient_id
[2] GPS เข้า                                               ▼
   มือถือส่ง GPS ดิบ → services/gps_processor.py
     → kalman_filter.smooth() ลด noise
     → crud.save_gps_point()  ─────────────────────►  ตาราง gps_data (ดิบ+smooth)
     → firebase.update_live_position() ────────────►  Firebase (ตำแหน่งสด, ไม่ลง PostgreSQL)

[3] Module 1 เรียนรู้ (เรียก analyze_behavior)
   อ่าน gps_data (get_gps_history 30 วัน)
     → preprocess + DBSCAN cluster_places
     → crud.upsert_behavioral_profile() ───────────►  ตาราง behavioral_profiles (known_places JSON)

[4] โมดูลปลายน้ำอ่านผลของ Module 1 + ประวัติ GPS
   Module 2 (predict)  : get_behavioral_profile + get_gps_history → PredictionResponse (ไม่เขียน DB)
   Module 5 (recommend): get_behavioral_profile + get_latest_gps → RecommendationResponse (ไม่เขียน DB)
   Module 3 (risk)     : get_behavioral_profile + get_gps_history + get_latest_gps
                          → คำนวณคะแนน → crud.save_risk_score() ──►  ตาราง risk_scores
                          → ถ้าฉุกเฉิน/GPS หาย → crud.save_alert() ──►  ตาราง alerts
   Module 4 (search)   : get_behavioral_profile + get_gps_history + get_latest_gps
                          → ประเมินพื้นที่ค้นหา → SearchAreaResponse (ไม่เขียนผลลง DB)
                          → ถ้า GPS หาย (detect_gps_gap ของ Module 3) → crud.save_alert() ──►  ตาราง alerts
```

**สรุปทิศทาง:**
- **GPS เข้า** → เก็บที่ `gps_data` (PostgreSQL) ; ตำแหน่งสดแยกไป Firebase
- **อ่านกลับ** → Module 1 อ่าน `gps_data` ไปสร้าง `behavioral_profiles` ; Module 2/3/4/5 อ่าน `behavioral_profiles` + `gps_data`
- **ผลลัพธ์ถูกบันทึก** → โปรไฟล์ที่เรียนรู้ลง `behavioral_profiles` (Module 1) ; คะแนนเสี่ยงลง `risk_scores` และการแจ้งเตือนลง `alerts` (Module 3)

---

## ⚠️ ข้อสังเกตจากโค้ดจริง (สอดคล้องกับ data_flow.md)

> **ข้อ 1 กับ 2 ของเดิมถูกลบทิ้งแล้ว (22 ส.ค.)** — ทั้งคู่เขียนว่า `POST /api/gps`
> ยังเป็น stub ในหน่วยความจำ และ `risk_scores`/`alerts` ยังไม่มีฝั่งอ่าน
> **ไม่จริงทั้งคู่แล้ว** ตั้งแต่ 19 และ 22 ส.ค. ตามลำดับ

1. **`known_places` มีผู้เขียนสองคน** — หมุดของผู้ดูแล (`api/places.py`,
   `source: "manual"`) และผลคลัสเตอร์ของ Module 1 (`behavior_pipeline`,
   `source: "learned"`) ทั้งคู่ merge ไม่ทับกัน และค่าที่เรียนรู้มาถูกปรับสเกลให้อยู่บน
   แกนเดียวกับหมุดก่อนผสม (กฎอยู่ที่ `ai/module1_behavior/known_places.py`) —
   ถ้าไม่ปรับ หมุดจะถูกกลบ ไม่ใช่ถูกลบ ซึ่งมองไม่เห็นจากหน้าจอ
2. **`alerts` เขียนทุกรอบที่เข้าเงื่อนไข ตั้งใจให้เป็นแบบนั้น** — มันคือ audit trail
   คนไข้ที่ยืนอยู่ในเขตอันตรายจะได้ 1 แถวต่อนาที การกันซ้ำอยู่ที่ชั้น push
   (`push_notifications`) ไม่ใช่ที่ตารางนี้
3. **ไม่มี migration tool** — `init_db()` เรียก `Base.metadata.create_all` ซึ่ง
   *สร้างตารางที่ยังไม่มี* แต่ **ไม่เพิ่มคอลัมน์ให้ตารางที่มีอยู่แล้ว** ตารางใหม่
   (`device_tokens`, `push_notifications`) จึงขึ้นเองตอนบูต แต่ถ้าวันไหนต้องเพิ่ม
   คอลัมน์ในตารางเดิม ต้องเขียนสคริปต์ ALTER เองเหมือน
   `scripts/migrate_add_synthetic_injected.py`
