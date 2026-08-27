# PathGuard AI — ชั้นฐานข้อมูล (Database Layer)

> ✅ **ตรวจครบทั้ง 14 ตารางเมื่อ 27 ส.ค. 2026** เดิมเอกสารนี้เขียนไว้ 22 ส.ค. และมีแค่ 7 ตาราง
> ทั้งที่บรรทัดแรกอ้างว่าดึงมาจาก `models.py` — เพิ่มที่ขาดครบแล้ว: `risk_factor_weights` ·
> `risk_thresholds` · `temporal_rules` · `rule_audit_log` · `danger_zones` · `pairing_codes` ·
> `trip_requests` และคอลัมน์ `users.severity_level`
> **ยึด `app/db/models.py` เป็นหลักเสมอ** ถ้าเอกสารนี้กับโค้ดไม่ตรงกัน โค้ดถูก

อ้างอิงจาก `app/db/models.py` (ORM/ตาราง), `app/db/crud.py` (ฟังก์ชันอ่าน/เขียนข้อมูลผู้ป่วย)
และ `app/db/rule_repository.py` (ฟังก์ชันอ่าน/เขียนฐานความรู้ของ Module 3)
ระบบใช้ **PostgreSQL** เก็บประวัติ/ผล AI ทั้งหมด ส่วน **Firebase Realtime DB** เก็บตำแหน่งสด (live) แยกต่างหาก ไม่อยู่ใน ORM นี้

> **นโยบาย transaction:** ฟังก์ชันใน `crud.py` ทำแค่ `flush` (กำหนด PK) แต่ **ไม่ commit** — ตัวที่ commit คือ dependency `get_db` ของ FastAPI เมื่อจบ request
> ข้อยกเว้นที่ตั้งใจ: `rule_repository` เขียน `rule_audit_log` **ใน transaction เดียวกับ**
> การแก้กฎเสมอ (design Q4) เพื่อไม่ให้ log กับสถานะกฎขัดกันได้เลย

---

## 0. ภาพรวม 14 ตาราง แบ่งเป็น 4 กลุ่ม

| กลุ่ม | ตาราง | ใครเป็นเจ้าของ |
|---|---|---|
| **ข้อมูลผู้ป่วย** | `users` · `gps_data` · `behavioral_profiles` | Module 1 + ชั้น API |
| **ผลลัพธ์ AI** | `risk_scores` · `alerts` | Module 3 + Module 4 |
| **ฐานความรู้ (KB) ของ Module 3** | `risk_factor_weights` · `risk_thresholds` · `temporal_rules` · `danger_zones` · `rule_audit_log` | `rule_repository.py` เท่านั้น |
| **การส่งถึงมือคน** | `device_tokens` · `push_notifications` · `pairing_codes` · `trip_requests` | ชั้น API + `services/notification.py` |

**กลุ่ม KB คือสิ่งที่ทำให้ Module 3 เป็น expert system ไม่ใช่โค้ดที่ฝังตัวเลขไว้** — น้ำหนัก
เกณฑ์ และกฎเชิงเวลาทั้งหมดอ่านจากฐานข้อมูลตอน runtime แก้ได้โดยไม่แตะโค้ดและไม่ deploy ใหม่
รายละเอียดพร้อมแหล่งอ้างอิงทางการแพทย์อยู่ที่ `module3_rule_based_system.md`

---

## 1. แต่ละตารางเก็บอะไร (ORM models)

### 1.1 ข้อมูลผู้ป่วย

#### `users` (model: `User`)
ข้อมูลผู้ใช้ทั้งผู้ป่วยและผู้ดูแล เป็น "แม่กุญแจ" ที่ทุกตารางอื่นอ้างถึงผ่าน FK `patient_id`
- คอลัมน์หลัก: `id` (PK, int ภายใน), `firebase_uid` (string จาก Firebase, unique, **NOT NULL**), `name`, `role` (`patient`/`caregiver`), `caregiver_id` (FK ชี้ผู้ดูแลในตารางเดียวกัน), `severity_level`, `created_at`
- **`severity_level`** (เพิ่ม 26 ส.ค.) — `1` = ระยะต้น · `2` = ระยะกลาง · `NULL` = ผู้ดูแลไม่ได้ระบุ
  ผู้อ่านสามราย: `search_radius_adjustment.adjust_radius` (ระยะกลางหดรัศมี 20% ระยะต้นขยาย 20%) ·
  `api/recommendation.py` (Level 1 ได้ 3 รายการ Level 2 ได้ 5) · `api/trip_requests.py`
  (เฉพาะ Level 2 ที่ต้องขออนุมัติเดินทาง) และคืนออกทาง `GET /api/patients/{id}` กับ `POST /api/pair`
  **ไม่มีตัวคูณ severity ในสูตรคะแนนเสี่ยง** ตั้งใจ — มันจะเป็นตัวเลขเดียวใน KB ที่ไม่มีแหล่งอ้างอิงรองรับ
- `firebase_uid` เป็น **NOT NULL UNIQUE** แม้กับผู้ป่วยที่ยังไม่เคยเปิดเครื่อง เพราะเซิร์ฟเวอร์
  เลือก uid ให้ล่วงหน้าตอนสร้างผู้ป่วย (ดู `pairing_codes`) — ทางเลือกอีกทางคือทำให้ nullable
  ซึ่งจะต้องไปเช็ค null ทุกจุดที่ resolve ตัวตนไปตลอดกาล เพื่อสถานะที่มีอายุไม่กี่นาที
- ความสัมพันธ์: 1 user → หลาย `gps_records`, `risk_scores`, `alerts`, `behavioral_profiles` (ลบ user แล้ว cascade ลบหมด)

#### `gps_data` (model: `GPSData`)
ประวัติ GPS ย้อนหลัง (≈30 วัน) ของผู้ป่วย — เก็บทั้งค่าดิบและค่าที่ผ่าน Kalman แล้ว
- คอลัมน์หลัก: `patient_id` (FK), `latitude`/`longitude` (ดิบ), `smooth_latitude`/`smooth_longitude` (Kalman), `accuracy` (m), `speed` (m/s), `altitude` (m), `direction` (องศา 0–359), `device_motion` (เช่น `walking`/`still`), `synthetic_injected` (bool — `True`=จุดที่ inject สังเคราะห์เพื่อ demo/ทดสอบ, `False`=ข้อมูลจริง), `recorded_at` (เวลาที่วัดจริง), `created_at`
- หมายเหตุ: ตำแหน่ง **สด** ไม่ได้เก็บที่นี่ — ไปอยู่ Firebase ; ตารางนี้คือ "ประวัติ" ที่ AI ใช้เรียนรู้

#### `behavioral_profiles` (model: `BehavioralProfile`)
โปรไฟล์พฤติกรรมที่ Module 1 เรียนรู้ — **หนึ่งแถวต่อหนึ่งผู้ป่วย** (`patient_id` unique) เป็นจุดเชื่อมกลางที่ Module 2/3/4/5 อ่านไปใช้
- คอลัมน์หลัก: `patient_id` (FK, unique), `known_places` (JSON), `routine_patterns` (JSON), `typical_range_km`, `last_trained_at`, `updated_at` (auto onupdate)
- **`known_places`** — หนึ่งรายการต่อสถานที่: `{cluster_id, place_name, latitude, longitude, visit_frequency, avg_stay_time, radius_m, source, is_home}`
  `source` เป็น `"manual"` (หมุดที่ผู้ดูแลปัก) หรือ `"learned"` (ผลคลัสเตอร์) — **มีผู้เขียนสองคน
  และห้ามลบของกันและกัน** กฎการผสมและการปรับสเกลอยู่ที่ `ai/module1_behavior/known_places.py`
  `place_name` มีเฉพาะหมุดที่คนปัก อัลกอริทึมตั้งชื่อสถานที่ไม่ได้
- **`routine_patterns`** — `[{hour, cluster_id, probability}]` กิจวัตรรายชั่วโมง
  **แก้ข้อความเดิม: ไม่ใช่ "ยังไม่ถูกตั้งค่า" อีกแล้ว** ตั้งแต่ 26 ส.ค. มีผู้เขียนคือ
  `ai/module1_behavior/routine_patterns.py` (สั่งด้วย `scripts/build_routine_patterns.py`)
  และ Module 5 ให้น้ำหนักปัจจัย `time_match` 0.25
  ⚠️ **คอลัมน์นี้ผูกกับ `known_places` ด้วย `cluster_id` ซึ่งถูกแจกใหม่ตามตำแหน่งทุกครั้งที่เขียนหมุด**
  `api/places.py` จึงล้างคอลัมน์นี้ทิ้งทุกครั้งที่หมุดเปลี่ยน ไม่งั้น "9 โมงมักอยู่วัด" จะกลายเป็น
  "9 โมงมักอยู่บ้าน" เงียบ ๆ ต้องรัน `build_routine_patterns` ใหม่หลังแก้หมุด

### 1.2 ผลลัพธ์ AI

#### `risk_scores` (model: `RiskScore`)
ผลคะแนนความเสี่ยงที่ Module 3 คำนวณในแต่ละครั้ง (เก็บเป็นประวัติ ไม่ทับของเก่า)
- คอลัมน์หลัก: `patient_id` (FK), `score` (0–100), `level` (`low`/`medium`/`high`), `wandering_detected` (bool), `gps_available` (bool), `factors` (JSON string ของ contributions แต่ละปัจจัย), `calculated_at`
- ประวัตินี้ไม่ได้มีไว้ดูเล่น — กฎเชิงเวลาใน `temporal_rules` อ่านมันย้อนหลัง (`get_recent_risk_scores`)
  และ `api/gps.py` อ่าน `calculated_at` ของแถวล่าสุดเป็นตัวจับเวลา throttle 60 วินาที

#### `alerts` (model: `Alert`)
การแจ้งเตือนที่เกิดขึ้น
- คอลัมน์หลัก: `patient_id` (FK), `alert_type`, `severity` (`low`/`medium`/`high`/`critical`), `message`, `latitude`/`longitude` (ตำแหน่งตอนเตือน), `resolved` (bool), `resolved_at`, `created_at`
- **`alert_type` มีหกค่า** (แก้ข้อความเดิมที่เขียนไว้สี่): `wandering` · `geofence` · `gps_loss` · `emergency` · `sos` · `trip_denied`
  รายการตัวจริงอยู่ที่ `app/models/alert.py` (`AlertType` / `ALERT_TYPES`) **ที่เดียว** และ
  `tests/test_alert_types.py` สแกน `app/api/` ด้วย `ast` เพื่อกันไม่ให้มีใครเขียนค่านอกลิสต์
  (เคยมี `gps_loss` กับ `gps_lost` อยู่คนละไฟล์ ทำให้ผู้ดูแลโดน push ซ้ำสองครั้งจาก GPS ขาดครั้งเดียว)

### 1.3 ฐานความรู้ (Knowledge Base) ของ Module 3

ห้าตารางนี้ **ไม่มีข้อมูลผู้ป่วยเลยสักคอลัมน์** — เป็นกฎของระบบ ใช้ร่วมกันทุกคน
สี่ตารางแรก (`risk_factor_weights` · `risk_thresholds` · `temporal_rules` · `danger_zones`)
ใช้ชุดคอลัมน์กำกับที่มาเหมือนกันหมด: `source_reference` (เช่น `"TH-DMS-2564 §BPSD"`),
`rationale` (เหตุผลที่คนอ่านรู้เรื่อง), `active`, `version`, `effective_from`, `created_by`
ส่วน `rule_audit_log` เป็นคนละแบบ — มันคือ*บันทึกการเปลี่ยน* ไม่ใช่กฎ จึงไม่มี `active`/`version`
**การแก้ไม่เคยเป็นการ UPDATE ทับ** — ปิด `active` ของแถวเดิมแล้วเขียนแถวใหม่ ประวัติจึงย้อนดูได้เสมอ

#### `risk_factor_weights` (model: `RiskFactorWeight`)
น้ำหนักของแต่ละปัจจัยในสูตรคะแนนเสี่ยง **ผลรวมของแถวที่ `active` ต้องเท่ากับ 1.0**
- คอลัมน์เฉพาะ: `factor_name` (ตรวจกับ `rule_repository.KNOWN_FACTORS`), `weight` (0–1)
- ปัจจุบัน 5 ปัจจัย: `route_deviation` 0.30 · `wandering` 0.25 · `confusion` 0.20 · `danger_zone` 0.15 · `unfamiliarity` 0.10
- ⚠️ **ห้ามฮาร์ดโค้ดตัวเลขพวกนี้ที่ไหนอีก** โหมด partial (ผู้ป่วยที่ยังไม่มีหมุด) เกลี่ยน้ำหนักใหม่
  **จากค่าใน KB ตอน runtime** ไม่ใช่จากค่าคงที่ในโค้ด — เพราะ endpoint แอดมินแก้ค่าพวกนี้ได้

#### `risk_thresholds` (model: `RiskThreshold`)
เกณฑ์ตัดที่มีชื่อ — เส้นแบ่งคะแนน ระยะทาง และเวลา
- คอลัมน์เฉพาะ: `threshold_name` (ตรวจกับ `KNOWN_THRESHOLDS`), `value`, `unit` (`score`/`meter`/`second`)
- **ปัจจุบันมี 7 ตัว**: `low_ceiling` · `medium_ceiling` · `emergency_score` · `route_deviation_ceiling_m` · `gps_gap_seconds` · `push_cooldown_seconds` · `sos_cooldown_seconds`
- 🛑 **`get_all_thresholds` จะไม่ยอมโหลดถ้าเกณฑ์ที่รู้จักขาดไปแม้ตัวเดียว** บนฐานข้อมูลที่ยัง
  ไม่ seed ใหม่ อาการที่ได้คือ **การคำนวณคะแนนเสี่ยงล้มทั้งระบบ** ไม่ใช่แค่ฟีเจอร์ที่เพิ่งเพิ่ม
  รัน `python -m app.mock.seed_risk_rules` ก่อน deploy — สคริปต์ idempotent ใส่เฉพาะแถวที่ขาด

#### `temporal_rules` (model: `TemporalRule`)
กฎที่ใช้ **ประวัติ** คะแนนของผู้ป่วย ไม่ใช่ค่าปัจจุบันค่าเดียว
- คอลัมน์เฉพาะ: `rule_name` (ตรวจกับ `KNOWN_TEMPORAL_RULES`), `parameters` (JSON — ค่าปรับได้ต่อกฎ)
- สองกฎ: `trend_escalation` (คะแนนไต่ขึ้นต่อเนื่อง → บวกเพิ่ม) · `sustained_high_risk` (สูงติดกันหลายรอบ → ยกระดับเป็นฉุกเฉิน)
- ค่าตัวเลขทั้งหมดอยู่ใน `parameters` เช่น `{"window": 5, "min_score": 50}` — เอนจิน
  `temporal_adjustment` เป็นฟังก์ชันบริสุทธิ์ ไม่มีตัวเลขฝังอยู่เลย

#### `danger_zones` (model: `DangerZone`)
วงกลม geofence ที่ถ้าผู้ป่วยเข้าไป จะ**บังคับให้เป็นเหตุฉุกเฉินทันที** ไม่ต้องรอ 5 รอบสะสม
- คอลัมน์หลัก: `name`, `center_latitude`/`center_longitude`, `radius_meters`, `zone_type` (`highway`/`waterway`/`construction`/`other`), `active`, `synthetic_injected`, + ชุดคอลัมน์กำกับที่มา
- ⚠️ **ไม่มีคอลัมน์ `patient_id` — เขตอันตรายเป็นของกลางทั้งระบบ** และ endpoint ทั้งสามตัว
  ตรวจแค่ "ล็อกอินอยู่" ไม่ได้ตรวจความเป็นเจ้าของ แปลว่า**ใครที่ล็อกอินได้ รวมถึงเครื่องของผู้ป่วยเอง
  ก็ `DELETE /api/danger-zones/{id}` ได้** ซึ่งเป็นการปิดเสียงเส้นทางเดียวที่เตือนทันที
  **ตัดสินใจเมื่อ 26 ส.ค. ว่ารับความเสี่ยงนี้** เพราะเป็น prototype ที่คนถือบัญชีมีแค่ครอบครัวกับทีม
  อันตรายจริงคือกดพลาด ไม่ใช่ผู้บุกรุก และ `DELETE` เป็น **soft deactivate** (ตั้ง `active=False`
  ผ่าน `rule_repository` พร้อม audit trail — แถวไม่เคยหายจริง กู้คืนและสืบย้อนได้)
  **สิ่งที่จะพลิกการตัดสินใจนี้:** มีมากกว่าหนึ่งครอบครัวในฐานข้อมูลเดียวกัน ตอนนั้น "ล็อกอินอยู่"
  จะเลิกแปลว่า "อยู่ในบ้านหลังนี้"

#### `rule_audit_log` (model: `RuleAuditLog`)
ร่องรอยการแก้กฎทุกครั้ง **เขียนใน transaction เดียวกับการแก้เสมอ** — log กับสถานะกฎขัดกันไม่ได้
- คอลัมน์หลัก: `table_name`, `record_id` (id ของแถว active **ใหม่**), `field_changed`, `old_value` (`NULL` เมื่อเป็นการเพิ่ม), `new_value` (`NULL` เมื่อเป็นการปิดใช้), `changed_by`, `changed_at`, `reason`
- **`reason` เป็น NOT NULL ตั้งใจ** — ไม่มีการแก้กฎแบบไม่บอกเหตุผล ถ้าจะเปลี่ยนตัวเลขที่มีงานวิจัย
  รองรับ ต้องเขียนไว้ว่าทำไม

### 1.4 การส่งถึงมือคน

#### `device_tokens` (model: `DeviceToken`)
FCM registration token ของเครื่องผู้ดูแลหนึ่งเครื่อง (22 ส.ค.)
- คอลัมน์หลัก: `user_id` (FK), `token` (unique), `platform` (`android`/`ios`/`web`), `created_at`, `last_seen_at`
- ผู้ดูแลหนึ่งคนมีได้หลายเครื่อง จึงเป็นความสัมพันธ์แบบหลายต่อหนึ่ง ตัว token เองเป็น unique
  เพราะ Firebase ออกใหม่ทุกครั้งที่ลงแอปใหม่ และแอปยิงซ้ำทุกครั้งที่เปิด — endpoint จึงเป็น upsert

#### `push_notifications` (model: `PushNotification`)
หนึ่งแถวต่อ push ที่ส่งจริง **และเป็น state ของ cooldown** (22 ส.ค.)
- คอลัมน์หลัก: `patient_id` (FK), `alert_id` (FK), `alert_type`, `recipients` (จำนวนเครื่องที่ถึง), `sent_at`
- **cooldown คิดต่อคู่ (ผู้ป่วย, ชนิดการเตือน)** ค่าเริ่มต้น 10 นาที อ่านจาก `push_cooldown_seconds`
  ใน KB — เหตุผลที่กันซ้ำอยู่ตรงนี้แทนที่จะเป็นที่ `alerts` อยู่ในข้อสังเกตท้ายไฟล์

#### `pairing_codes` (model: `PairingCode`)
รหัสสั้นที่ผู้ดูแลอ่านจากหน้าจอตัวเองไปกรอกบนเครื่องผู้ป่วย (26 ส.ค.)
- คอลัมน์หลัก: `code` (unique, เก็บแบบ normalise แล้ว — ตัวใหญ่ ไม่มีขีด), `patient_id` (FK), `expires_at`, `used_at` (`NULL` = ยังไม่ถูกใช้), `created_at`
- **อายุ 24 ชั่วโมง ใช้ได้ครั้งเดียว** และแถวที่ใช้แล้ว**ไม่ถูกลบ** เพื่อให้ log แยกออกว่า
  "รหัสนี้ถูกใช้ไปแล้ว" กับ "ไม่มีรหัสนี้" ต่างกัน โดยที่คำตอบที่ส่งกลับไปหาผู้เรียกเหมือนกันทั้งคู่ (`404`)
- **ทำไมเป็น 8 ตัวอักษร ไม่ใช่ 6 หลัก** — สิ่งที่อยู่หลังประตูนี้คือตำแหน่งสดของผู้ป่วยสมองเสื่อม
  และ `POST /api/pair` เป็น route เดียวที่ต้องทำงานได้ก่อนผู้เรียกจะมี token **จึงไม่มีตัวนับการเดาผิด
  และไม่มี rate limiting** (ตัวนับใน process เดียวจะโกหกทันทีที่มี worker ตัวที่สอง)
  ความปลอดภัยจึงมาจาก entropy ล้วน ๆ: 8 ตัวจากอักษร 30 ตัว ≈ 6.6×10¹¹ ส่วน 6 หลักคือ 10⁶
  **การเปลี่ยนไปใช้ตัวเลข 6 หลักต้องสร้าง rate limiting ก่อน**

#### `trip_requests` (model: `TripRequest`)
คำขออนุมัติเดินทางของผู้ป่วย Level 2 (C-3 ในรายงาน, 26 ส.ค.)
- คอลัมน์หลัก: `patient_id` (FK), `destination_name`, `latitude`/`longitude`, `confidence` (0–1), `factors` (JSON), `status` (`pending`/`approved`/`rejected`), `decided_by` (FK ผู้ดูแล), `decided_at`, `created_at`
- **`confidence` ถูกแช่ไว้ตอนขอ ไม่คำนวณใหม่ตอนผู้ดูแลเปิดดู** เพราะผู้ดูแลกำลังตัดสินใจเกี่ยวกับ
  *นาทีที่ผู้ป่วยขอ* คะแนนที่ไหลไปเรื่อยระหว่างมือถืออยู่ในกระเป๋าจะเป็นคนละคำถาม
- ค่ามาจาก `ai/module5_recommend/trip_confidence.py` **ไม่ใช่ `score_place`** — `score_place`
  วัดความคุ้นเคยเทียบกับที่ที่เคยไป แต่ C-3 มีอยู่เพราะผู้ป่วยขอไปที่ที่**ไม่เคยไป** เพดานจึงตันที่ 0.350 ตลอดกาล
- **ไม่มี timeout อัตโนมัติ ตั้งใจ** — คำขอที่หมดอายุเองจะหน้าตาเหมือนคำขอที่ไม่มีใครเห็น
  และความต่างนั้นสำคัญกับครอบครัว

---

## 2. ใครเขียน / ใครอ่าน แต่ละตาราง

### 2.1 ข้อมูลผู้ป่วยและผลลัพธ์ AI

| ตาราง | เก็บอะไร | ใครเขียน | ใครอ่าน |
|---|---|---|---|
| `users` | ผู้ป่วย/ผู้ดูแล + FK กลาง | `api/users.py` (`POST /api/register`) ; **`api/pairing.py` (`POST /api/patients` — ผู้ดูแลสร้างผู้ป่วย)** ; `seed_module5.py` | `api/users.py` · `api/gps.py` (resolve `firebase_uid`→`id`) · **`services/auth.py` ทุก request** · **`api/pairing.py` (`GET /api/patients/{id}`)** · `api/recommendation.py` + `api/search_area.py` + `api/trip_requests.py` (อ่าน `severity_level`) |
| `gps_data` | ประวัติ GPS ดิบ+smooth | **Module 1** `services/gps_processor.py` ; `scripts/import_geolife.py` ; `scripts/inject_wandering.py` | **Module 1** `behavior_pipeline.py` · **Module 2** `destination_prediction.py` · **Module 3** `api/risk.py` · **Module 4** `api/search_area.py` (ผ่าน `get_gps_history`) ; `get_latest_gps` อ่านโดย Module 3/4/5 ; **`api/tracking.py`** (`GET /api/patients/{id}/track` — แผนที่ของผู้ดูแล) |
| `behavioral_profiles` | สถานที่/กิจวัตรที่เรียนรู้ | **Module 1** `behavior_pipeline.py` ; **`api/places.py`** (หมุดของผู้ดูแล — `POST .../places`, `PUT .../places/home`) ; **`scripts/build_routine_patterns.py`** (`routine_patterns`) ; `seed_module5.py` | Module 2/3/4/5 ผ่าน `get_behavioral_profile` |
| `risk_scores` | ผลคะแนนเสี่ยง 0–100 | **Module 3** `api/risk.py` — ทุกครั้งที่ GPS เข้า (throttle 60 วิ) และทุกครั้งที่มีคนเรียก `GET /api/risk/{id}` | `api/gps.py` (จับเวลา throttle) ; `get_recent_risk_scores` ใช้โดยกฎ temporal |
| `alerts` | การแจ้งเตือน | **Module 3** `api/risk.py` (emergency + gps_loss) · **Module 4** `api/search_area.py` (gps_loss) · **`api/sos.py`** (`sos`) · **`api/trip_requests.py`** (`trip_denied`) | **`api/alerts.py`** (`GET /api/patients/{id}/alerts`, `PATCH /api/alerts/{id}`) ; `services/notification.py` |

### 2.2 ฐานความรู้ — เขียนผ่าน `rule_repository.py` เท่านั้น

| ตาราง | ใครเขียน | ใครอ่าน |
|---|---|---|
| `risk_factor_weights` | `rule_repository.update_weights` / `update_weight` ← `api/admin_rules.py` | `get_active_weights` ← `api/risk.py` ทุก request (ไม่มี cache — แก้กฎแล้วมีผลกับ request ถัดไปทันที) |
| `risk_thresholds` | `rule_repository.update_threshold` ← `api/admin_rules.py` ; `app/mock/seed_risk_rules.py` | `get_all_thresholds` / `get_threshold` ← `api/risk.py` · `api/search_area.py` · `api/sos.py` (`services/notification.py` **ไม่ได้อ่านเอง** — ผู้เรียกส่งค่า cooldown เข้าไปให้) |
| `temporal_rules` | `rule_repository.update_temporal_rule` ← `api/admin_rules.py` | `get_active_temporal_rules` ← `api/risk.py` |
| `danger_zones` | `rule_repository.add_danger_zone` / `deactivate_danger_zone` ← `api/danger_zones.py` ; `scripts/inject_wandering.py` (`synthetic_injected=True`) | `get_active_danger_zones` ← `api/risk.py` |
| `rule_audit_log` | `rule_repository._audit` — **ทุกฟังก์ชันเขียนกฎข้างบนเขียนลงนี่ใน transaction เดียวกัน** | `api/admin_rules.py` (`GET /api/admin/rules/history`) |

### 2.3 การส่งถึงมือคน

| ตาราง | ใครเขียน | ใครอ่าน |
|---|---|---|
| `device_tokens` | `api/devices.py` (`POST /api/devices/token`, upsert) | `services/notification.py` (`get_caregiver_tokens`) |
| `push_notifications` | `services/notification.py` (`record_push`) | `services/notification.py` (`seconds_since_last_push` — เช็ค cooldown) |
| `pairing_codes` | `api/pairing.py` — `POST /api/patients` สร้าง · `POST /api/pair` ตั้ง `used_at` | `api/pairing.py` (`get_pairing_code`) |
| `trip_requests` | `api/trip_requests.py` — `POST /api/trip-requests` สร้าง · `PATCH /api/trip-requests/{id}` ตัดสิน | `api/trip_requests.py` (`GET /api/patients/{id}/trip-requests`) |

> หมายเหตุ `get_user_id_by_firebase_uid` ใช้แปลง `firebase_uid` (string) → `users.id` (int) ก่อนเขียน GPS เพราะ `gps_data.patient_id` เป็น FK แบบ int

---

## 3. ข้อมูลไหลและคงอยู่อย่างไร (Persistence flow)

```
[0] ผู้ดูแลสร้างผู้ป่วย + จับคู่เครื่อง
   ผู้ดูแล → POST /api/patients → users (เซิร์ฟเวอร์เลือก firebase_uid เอง)
                                → pairing_codes (รหัส 8 ตัว อายุ 24 ชม.)
   เครื่องผู้ป่วย → POST /api/pair {code} → ตั้ง used_at → คืน Firebase custom token
                                → signInWithCustomToken() → เป็น client ธรรมดาตลอดไป

[1] ลงทะเบียนผู้ดูแล
   Flutter → POST /api/register → users (สร้าง users.id) ──┐
                                                           │ FK patient_id
[2] GPS เข้า                                               ▼
   มือถือส่ง GPS ดิบ → services/gps_processor.py
     → kalman_filter.smooth() ลด noise
     → crud.save_gps_point()  ─────────────────────►  ตาราง gps_data (ดิบ+smooth)
     → firebase.update_live_position() ────────────►  Firebase (ตำแหน่งสด, ไม่ลง PostgreSQL)
     → _score_risk_after_ingest() (throttle 60 วิ) ─►  เรียก [4] Module 3 ให้เอง

[3] แหล่งของ known_places — สองทาง ไม่ใช่ทางเดียว
   ผู้ดูแลปักหมุด → api/places.py ────────────────►  behavioral_profiles.known_places (source: manual)
   Module 1 เรียนรู้ (analyze_behavior) ──────────►  behavioral_profiles.known_places (source: learned)
   ⚠️ ทางที่สอง **ไม่มีใครเรียกใน production** ตั้งใจ (ตัดสินใจ 26 ส.ค.) — DBSCAN บน GPS ดิบ
      ให้ "สถานที่" 142–156 แห่งที่ไม่มีชื่อ และชี้ทีมค้นหาไปที่ centroid ของถนน
      โค้ดยังอยู่เพื่อรายงานผลการทดลอง ไม่ใช่เพื่อรัน

[4] โมดูลปลายน้ำอ่านผลของ [3] + ประวัติ GPS + ฐานความรู้
   Module 3 (risk)     : get_behavioral_profile + get_gps_history + get_latest_gps
                          + rule_repository (น้ำหนัก/เกณฑ์/เขตอันตราย/กฎเชิงเวลา)
                          → คำนวณคะแนน → crud.save_risk_score() ──►  ตาราง risk_scores
                          → ถ้าฉุกเฉิน/GPS หาย → crud.save_alert() ──►  ตาราง alerts
                                               → notify_alert() ────►  push_notifications + FCM
   Module 4 (search)   : + rule_repository (gps_gap_seconds) + users.severity_level
                          → SearchAreaResponse (ไม่เขียนผลลง DB) แต่เขียน alert ถ้า GPS หายจริง
   Module 5 (recommend): get_behavioral_profile + get_latest_gps + users.severity_level
                          → RecommendationResponse (ไม่เขียน DB)
   Module 2 (predict)  : **ไม่ได้ mount** — ถูกตัดพร้อม TensorFlow

[5] ขออนุมัติเดินทาง (เฉพาะ Level 2)
   เครื่องผู้ป่วย → POST /api/trip-requests → trip_confidence.py → trip_requests (แช่ confidence ไว้)
   ผู้ดูแล → PATCH /api/trip-requests/{id} → ตั้ง status/decided_by/decided_at
                                            → ถ้าปฏิเสธ เขียน alerts (trip_denied)
```

**สรุปทิศทาง:**
- **GPS เข้า** → เก็บที่ `gps_data` (PostgreSQL) ; ตำแหน่งสดแยกไป Firebase
- **known_places มาจากหมุดของคน ไม่ใช่จากการเรียนรู้** — นี่คือการตัดสินใจ ไม่ใช่สถานะชั่วคราว
- **ผลลัพธ์ถูกบันทึก** → คะแนนเสี่ยงลง `risk_scores` และการแจ้งเตือนลง `alerts` (Module 3)
- **กฎแยกจากโค้ด** → น้ำหนัก/เกณฑ์/เขตอันตราย/กฎเชิงเวลาอยู่ในฐานข้อมูล อ่านสดทุก request

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
   (`device_tokens`, `push_notifications`, `pairing_codes`, `trip_requests`) จึงขึ้นเอง
   ตอนบูต แต่คอลัมน์ใหม่ในตารางเดิมไม่ขึ้น ต้องเขียนสคริปต์ ALTER เอง — มีสองตัวแล้ว:
   `scripts/migrate_add_synthetic_injected.py` และ **`scripts/migrate_add_severity_level.py`**
   ✅ **รันกับ Neon แล้ว 27 ส.ค. 2026** — และการรันครั้งนั้นพิสูจน์ข้อความข้างบนพอดี:
   `init_db` สร้าง `pairing_codes` กับ `trip_requests` ที่ยังไม่มีบน Neon ให้เองตอนบูต
   ส่วน `users.severity_level` ต้องรอ `ALTER TABLE` จากสคริปต์ ไม่มีทางขึ้นเอง
4. **ฐานความรู้ต้อง seed ก่อน ไม่ใช่ตัวเลือก** — `python -m app.mock.seed_risk_rules`
   ดูเหตุผลที่ `risk_thresholds` ข้างบน: เกณฑ์ขาดตัวเดียว = คะแนนเสี่ยงล้มทั้งระบบ
5. **การแก้กฎไม่เคยเป็นการ UPDATE ทับ** — ปิด `active` แถวเดิม เขียนแถวใหม่ พร้อมแถวใน
   `rule_audit_log` ใน transaction เดียวกัน ถ้าเห็นโค้ดที่ `UPDATE risk_factor_weights SET weight=...`
   ตรง ๆ นั่นคือบั๊ก
