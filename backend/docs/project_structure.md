# PathGuard AI — โครงสร้างโปรเจกต์ (backend/app)

> ✅ **ไล่กับ `find` จริงเมื่อ 27 ส.ค. 2026** เดิมเขียนไว้ 22 ส.ค. และคลาดเคลื่อนสองทาง
> ไม่ใช่ทางเดียว — **ขาดไฟล์ที่มีจริง 9 ตัว** และ **ระบุไฟล์ที่ไม่มีอยู่แล้ว**:
> `sequence_learning.py` ถูกลบไปพร้อมการตัด TensorFlow ส่วน `data_source.py` กับ
> `weather_provider.py` ย้ายจาก `module5_recommend/` ไป `app/mock/`
> ♻️ **ไล่ซ้ำ 28 ส.ค. 2026**: `models.py` เป็น 16 ตาราง · `users.py` มี `PUT /api/caregivers/{id}/location`
> · `pairing.py` มีรหัสเชิญผู้ดูแลสองเส้น
> **`ls` ที่โฟลเดอร์จริงคือคำตอบที่ถูกเสมอ** อันนี้ใช้ดูว่าแต่ละส่วนทำหน้าที่อะไร

โครงสร้างไฟล์จริงของ backend พร้อมคำอธิบายภาษาไทยทุกโฟลเดอร์และทุกไฟล์
(ชื่อไฟล์/โฟลเดอร์เป็นภาษาอังกฤษ คำอธิบายเป็นภาษาไทย)

> หมายเหตุ: โมดูล AI ครบทั้ง 1–5 มีโค้ดจริงแล้ว — แต่ **Module 2 ไม่ได้ถูก mount**
> (`api/prediction.py` ยังอยู่ แต่ `app/main.py` ไม่ include แล้ว เพราะตัด TensorFlow ออก)
> และ **Module 1 ไม่มีใครเรียกใน production** ตั้งใจ ดู `plan_person3_lane.md`

```
backend/
├── main.py                          — launcher บาง ๆ ให้ `uvicorn main:app` ทำงานได้ (import app จาก app/main.py)
├── seed_module5.py                  — shim บาง ๆ ตัวจริงย้ายไป app/mock/seed_module5.py แล้ว เก็บไว้ให้คำสั่งเดิมยังรันได้
├── requirements.txt                 — dependency หลัก (fastapi, sqlalchemy, scikit-learn ฯลฯ — `tensorflow` ถูก comment ทิ้ง)
├── requirements-dev.txt             — dependency สำหรับทดสอบ (pytest, pytest-asyncio, aiosqlite)
│
├── API_CONTRACT_APP.md              — สัญญากับแอปมือถือ 11 หัวข้อ (คนที่ 1 อ่านตัวนี้) — อยู่ที่ root เพราะ docs/ เคยถูก gitignore
├── API_CONTRACT_ADMIN.md            — สัญญาของ endpoint ฝั่งแอดมิน (หมุดสถานที่, เขตอันตราย)
├── REPORT_VS_CODE.md                — ผลกวาดรายงานเทียบกับโค้ดจริงทีละข้อ **อ่านก่อนรับปากว่าฟีเจอร์ไหนมีอยู่**
├── APP_SYNC_2026-08-27.md           — บันทึกการคุยข้ามทีมกับคนที่ 1 (คำถาม/คำตอบ + ใครต้องทำอะไร)
│
├── test_kalman_tuning.py            — สคริปต์เดี่ยว (ไม่ใช่ pytest) วัดพารามิเตอร์ Q/R + adaptive jump detection
├── test_route_prediction.py         — สคริปต์เดี่ยว ทดสอบ HMM/Viterbi ของ route_prediction
├── test_wandering_detection.py      — สคริปต์เดี่ยว ทดสอบ Isolation Forest ของ wandering_detection
│                                      ⚠️ สามตัวนี้ชื่อขึ้นต้น test_ แต่ pytest ไม่ได้เก็บ — รันมือ
│
├── scripts/                         — data pipeline + งาน ops (ไม่แตะโค้ด AI)
│   ├── import_geolife.py            — โหลดข้อมูลจริง GeoLife (.plt) → derive speed/direction → remap เวลา → bulk-load เข้า gps_data
│   ├── inject_wandering.py          — เติมจุด pacing สังเคราะห์ + danger zone (synthetic_injected=True) ให้มีพฤติกรรมหลงทางตรวจจับ
│   ├── build_routine_patterns.py    — สร้าง routine_patterns (กิจวัตรรายชั่วโมง) จากประวัติ GPS + หมุด (26 ส.ค.) — ต้องรันใหม่ทุกครั้งที่หมุดเปลี่ยน
│   ├── migrate_add_synthetic_injected.py — migration เพิ่มคอลัมน์ synthetic_injected (gps_data + danger_zones)
│   ├── migrate_add_severity_level.py — migration เพิ่มคอลัมน์ users.severity_level (26 ส.ค.) ✅ รันกับ Neon แล้ว 27 ส.ค.
│   ├── demo_run.py                  — terminal demo: รัน Module 1–5 ต่อผู้ป่วย แล้ว print narrative (TF-free)
│   ├── demo_server.py               — เซิร์ฟเวอร์ dashboard ผู้ดูแล (mount router จริงชุดเดียวกับ app/main.py + เสิร์ฟ dashboard.html)
│   ├── dashboard.html               — หน้า dashboard ภาษาไทย: แผนที่ Leaflet, เกจความเสี่ยง, ฟีดแจ้งเตือน
│   ├── measure_learning_days.py     — วัดว่าต้องเก็บ GPS กี่วันชุดสถานที่จึงนิ่ง + prototype stay-point (`--stops`)
│   └── README.md                    — ลำดับการรัน pipeline (migrate → seed → import → inject → demo)
│
└── app/
    ├── main.py                      — จุดเริ่มต้นแอป FastAPI: init_db + init_firebase ตอน startup + mount router ทุกตัว
    │
    ├── ai/                          — โมดูล AI ทั้งหมด (สมองของระบบ ทำงานเรียงลำดับ 1→5)
    │   ├── lstm_utils.py            — โมเดล LSTM ที่ใช้ร่วมกัน ทำนายคลัสเตอร์ตำแหน่งถัดไป (ไม่ถูกเรียกใน production — ตัด TF)
    │   │
    │   ├── module1_behavior/        — เรียนรู้พฤติกรรม/กิจวัตรการเดินทางจากประวัติ GPS
    │   │   ├── data_collection.py        — (dead code ตั้งแต่ 19 ส.ค.) เก็บ GPS ลง list ในหน่วยความจำ ไม่มีใครเรียกแล้ว
    │   │   ├── known_places.py           — **เจ้าของกฎ known_places** ผสมหมุดผู้ดูแล + ผลคลัสเตอร์ พร้อมปรับสเกลให้อยู่แกนเดียวกัน (22 ส.ค.)
    │   │   ├── routine_patterns.py       — สร้าง/อ่านกิจวัตรรายชั่วโมง + `local_hour()` ที่ทุกฝั่งใช้ร่วมกัน (26 ส.ค.)
    │   │   ├── data_preprocessing.py     — ทำความสะอาดข้อมูล GPS + กรอง noise ด้วย Kalman ก่อนส่งให้ DBSCAN
    │   │   ├── place_clustering.py       — จัดกลุ่มสถานที่ด้วย DBSCAN (haversine) + คำนวณเวลาพำนัก
    │   │   │                               ⚠️ วัดแล้วว่าได้ 142–156 "สถานที่" ไม่มีชื่อ ก้อนใหญ่สุดกว้าง 1.5 กม.
    │   │   │                               **WONTFIX ตั้งใจ** เก็บไว้เป็นผลการทดลองในรายงาน ไม่ใช่ของที่ใช้จริง
    │   │   └── behavior_pipeline.py      — ตัวเชื่อม DB↔AI: อ่านประวัติ GPS → preprocess → cluster → merge กับหมุด → เขียนโปรไฟล์
    │   │                                   ⚠️ **ห้ามตั้งเวลาเรียก** สิ่งที่มันเรียนรู้ไม่ถูกต้อง (ดูบรรทัดบน)
    │   │
    │   ├── module2_prediction/      — ตรวจจับการเดินหลงทางและทำนายปลายทาง/เส้นทาง
    │   │   ├── cluster_matcher.py             — ระยะ Haversine + หาคลัสเตอร์ที่คุ้นเคยที่ใกล้ที่สุด (เคารพ radius_m ของแต่ละหมุด)
    │   │   ├── destination_prediction.py      — ใช้ LSTM ทำนายปลายทาง — **ไม่ได้ mount** (ตัด TensorFlow)
    │   │   ├── route_prediction.py            — HMM + Viterbi + DTW ทำนายเส้นทาง
    │   │   ├── stop_confusion_classification.py — จำแนกการหยุดว่าปกติหรือสับสน ด้วย Gradient Boosting (5 features)
    │   │   └── wandering_detection.py         — ตรวจจับพฤติกรรมเดินวนหลงทางด้วย Isolation Forest (5 features)
    │   │
    │   ├── module3_risk/            — คำนวณคะแนนความเสี่ยง 0–100 และตัดสินใจแจ้งเหตุฉุกเฉิน
    │   │   ├── risk_data_collection.py     — รวบรวมปัจจัยเสี่ยง 5 ตัว โดยเรียกตัวตรวจจับของ Module 2
    │   │   ├── data_normalization.py       — ปรับค่าตัวแปรเสี่ยง 5 ตัวให้อยู่ในช่วง [0,1] ก่อนคิดคะแนน
    │   │   ├── risk_score_calculation.py   — คำนวณคะแนน 0–100 จากน้ำหนักที่**อ่านมาจากฐานความรู้** ไม่ใช่ค่าคงที่ในโค้ด
    │   │   ├── temporal_adjustment.py      — กฎที่ใช้ *ประวัติ* คะแนน (trend / sustained) เป็นฟังก์ชันบริสุทธิ์ ค่าปรับมาจาก temporal_rules
    │   │   ├── emergency_decision_engine.py — ตัดสินว่าต้องแจ้งฉุกเฉินไหม (เขตอันตราย → ทันที · คะแนน > เกณฑ์ → ทันที · สูงติดกัน 5 รอบ → ยกระดับ)
    │   │   └── gps_failure_handling.py     — ตรวจช่องว่างสัญญาณ GPS ที่นานเกินเกณฑ์ + เก็บตำแหน่งสุดท้าย
    │   │
    │   ├── module4_search_area/     — ประเมินพื้นที่ค้นหาเมื่อ GPS หาย (ไฟล์ AI ไม่ import กันเอง — router search_area.py เป็นตัวร้อยเรียง)
    │   │   ├── _geo.py                      — ฟังก์ชันเรขาคณิตที่ใช้ร่วมกันในโมดูลนี้
    │   │   ├── last_known_position.py       — ดึงตำแหน่งล่าสุด + คำนวณรัศมีฐาน (Distance = Speed × Time)
    │   │   ├── movement_path_simulation.py  — จำลองเส้นทาง 10,000 เส้นด้วย Monte Carlo + A*
    │   │   ├── probability_area_estimation.py — ประเมินโซน High/Medium/Low ด้วย KDE (กริด 50×50)
    │   │   └── search_radius_adjustment.py  — ปรับรัศมีตาม wandering score + ความหนาแน่นของหมุด + **ระดับอาการ (severity_level)**
    │   │                                      กฎสำคัญ: การขยายทบกันได้ แต่**การหดเลือกอันที่น้อยที่สุด** ไม่ทบ
    │   │
    │   └── module5_recommend/       — สร้างและจัดลำดับคำแนะนำสถานที่ (มี learned ranker)
    │       ├── user_context_analysis.py        — รวมโปรไฟล์ + ตำแหน่ง + เวลา เป็นออบเจ็กต์ UserContext
    │       ├── recommendation_generation.py    — ให้คะแนนสถานที่แบบกฎถ่วงน้ำหนัก หรือใช้ learned ranker ถ้ามี (ติดธง scorer=ml/rules)
    │       ├── recommendation_prioritization.py — เรียงลำดับและคัด N อันดับ (3 สำหรับ Level 1 · 5 สำหรับ Level 2)
    │       ├── trip_confidence.py               — คะแนนความมั่นใจของ **ที่ที่ไม่เคยไป** สำหรับ C-3 (26 ส.ค.)
    │       │                                      แยกจาก score_place เพราะ score_place ตันที่ 0.350 กับที่ที่ไม่เคยไป
    │       ├── ranker.py                        — Learned Pointwise Ranker (HistGradientBoosting) + เก็บ .pkl ต่อผู้ป่วย
    │       ├── featurize.py                     — สร้าง 8 features คงที่ให้ ranker
    │       └── evaluation.py                    — ชุดวัดความซื่อสัตย์ของโมเดล (temporal split, bootstrap CI, baselines, go/no-go)
    │
    ├── api/                         — ชั้น endpoint ที่รับ request จากแอป Flutter (25 route)
    │   ├── users.py                 — POST /api/register ลงทะเบียนผู้ใช้ใหม่จาก Firebase UID · PUT /api/caregivers/{id}/location เก็บตำแหน่งล่าสุดของผู้ดูแล · GET /api/patients/{id}/caregivers เรียงผู้ดูแลตามระยะทาง (A4, 28 ส.ค.)
    │   ├── pairing.py               — POST /api/patients (ผู้ดูแลสร้างผู้ป่วย + ออกรหัส) · POST /api/pair (แลกรหัสเป็น custom token) · GET /api/patients/{id} (ชื่อ + ระดับอาการ) · POST /api/patients/{id}/caregiver-invites + POST /api/caregivers/redeem-invite (ผู้ดูแลคนที่สอง, 28 ส.ค.) (26–28 ส.ค.)
    │   ├── gps.py                   — POST /api/gps + /api/gps/batch บันทึก GPS ผ่าน gps_processor แล้วสั่งคิด risk เอง (throttle 60 วิ)
    │   ├── prediction.py            — GET /api/predict-destination/{id} — **ไม่ได้ mount** (ตัด TensorFlow) โค้ดยังอยู่
    │   ├── recommendation.py        — GET /api/recommendation/{id} สถานที่ที่น่าจะไป พร้อมชื่อและคะแนนความมั่นใจ
    │   ├── risk.py                  — GET /api/risk/{id} คำนวณ บันทึก และแจ้งเตือน ⚠️ GET ที่เขียนข้อมูลและยิง push
    │   ├── search_area.py           — GET /api/search-area/{id} ประเมินพื้นที่ค้นหา ⚠️ GET ที่เขียน alert และยิง push
    │   ├── admin_rules.py           — GET /api/admin/rules (+/history) อ่านฐานความรู้กฎ + ประวัติการแก้
    │   ├── places.py                — POST/GET /api/patients/{id}/places (ทั้งชุด) · PUT .../places/home (เฉพาะบ้าน ลบตัวอื่นไม่ได้)
    │   ├── danger_zones.py          — POST/GET/DELETE /api/danger-zones เขตอันตราย (ใช้ร่วมทุกคนไข้ ไม่มี patient_id)
    │   ├── devices.py               — POST /api/devices/token เก็บ FCM token ของเครื่องผู้ดูแล (22 ส.ค.)
    │   ├── tracking.py              — GET /api/patients/{id}/track เส้นทางล่าสุดให้แอปวาดแผนที่ (22 ส.ค.)
    │   ├── alerts.py                — GET /api/patients/{id}/alerts + PATCH /api/alerts/{id} ปิดแจ้งเตือน (22 ส.ค.) · POST/DELETE /api/alerts/{id}/claim "ฉันจะไปรับ" (A5, 28 ส.ค.)
    │   ├── sos.py                   — POST /api/sos ปุ่มฉุกเฉิน ข้ามการคิดคะแนนทั้งหมด cooldown ของตัวเอง 60 วิ (26 ส.ค.)
    │   └── trip_requests.py         — POST /api/trip-requests · GET /api/patients/{id}/trip-requests · PATCH /api/trip-requests/{id} (C-3, 26 ส.ค.)
    │
    ├── db/                          — จัดการฐานข้อมูล PostgreSQL + Firebase
    │   ├── database.py              — engine PostgreSQL/asyncpg (รองรับ Neon) + เริ่ม Firebase Admin SDK
    │   ├── models.py                — โมเดล ORM **16 ตาราง** (เพิ่ม patient_caregivers + caregiver_invites 28 ส.ค. — ดู database_layer.md)
    │   ├── rule_repository.py       — อ่าน/แก้ฐานความรู้กฎ (น้ำหนัก, threshold, เขตอันตราย, temporal) พร้อม audit log ใน transaction เดียวกัน
    │   └── crud.py                  — ฟังก์ชันอ่าน/เขียนข้อมูลผู้ป่วย (user, GPS, risk score, alert, profile, token, pairing, trip request, ลิงก์ผู้ดูแล + รหัสเชิญ + ตำแหน่งผู้ดูแล)
    │
    ├── mock/                        — ข้อมูลตั้งต้นและตัวจ่ายข้อมูลจำลอง (ย้ายมาจาก module5_recommend/ เพื่อไม่ให้ปนกับโค้ด AI)
    │   ├── seed_risk_rules.py       — 🛑 **ต้องรันก่อน deploy** ใส่น้ำหนัก/เกณฑ์/กฎเชิงเวลาลงฐานความรู้ (idempotent)
    │   ├── seed_module5.py          — ใส่โปรไฟล์ + สถานที่จำลองเพื่อทดสอบ Module 5 ก่อนมี GPS จริง
    │   ├── data_source.py           — สร้างข้อมูลจำลอง (seeded) สำหรับฝึกและประเมิน ranker
    │   └── weather_provider.py      — ตัวจ่ายสภาพอากาศจำลอง (seeded) สำหรับฝึก/ทดสอบ
    │
    ├── models/                      — Pydantic schema สำหรับ validate request/response ของ API
    │   ├── user_profile.py          — UserCreate, UserResponse, BehavioralProfileResponse
    │   ├── gps_data.py              — GPSDataCreate, GPSDataResponse, LiveGPSUpdate
    │   ├── risk_score.py            — RiskScoreCreate, RiskScoreResponse
    │   ├── alert.py                 — AlertCreate, AlertResponse, AlertResolve + **AlertType/ALERT_TYPES ซึ่งเป็นรายการชนิดการเตือนที่เดียวของระบบ**
    │   ├── search_area.py           — SearchAreaResponse, ProbabilityZone, FamiliarPath, TargetLocation, GridBounds
    │   ├── prediction.py            — PredictionResponse, TopPrediction (คู่กับ router ที่ไม่ได้ mount)
    │   └── recommendation.py        — RecommendationResponse, RecommendedPlace, RecommendationFlags
    │
    └── services/                    — บริการพื้นฐานที่โมดูลอื่นเรียกใช้
        ├── kalman_filter.py         — Kalman filter 2D แบบสด (ทีละจุด) ติดตาม position + velocity
        ├── kalman_batch.py          — Kalman filter 1D แบบ batch ย้อนหลัง + adaptive jump detection
        ├── gps_processor.py         — orchestrator รับ GPS: smooth → บันทึก PostgreSQL → ส่ง live update ขึ้น Firebase
        ├── firebase.py              — update_live_position เขียนตำแหน่งสดขึ้น Firebase Realtime DB
        ├── notification.py          — ยิง FCM ถึงผู้ดูแล + cooldown ต่อคู่ (ผู้ป่วย, ชนิดการเตือน) (22 ส.ค.)
        └── auth.py                  — ตรวจ Firebase ID token + สิทธิ์เข้าถึงคนไข้ (22 ส.ค., `AUTH_ENABLED` ปิดอยู่)
```

## ไฟล์ที่ **ไม่มีแล้ว** แต่เคยอยู่ในเอกสารฉบับก่อน

| ที่เคยเขียนไว้ | ความจริง |
|---|---|
| `module1_behavior/sequence_learning.py` | ไม่มีแล้ว — ตัวห่อ LSTM ถูกลบพร้อมการตัด TensorFlow |
| `module5_recommend/data_source.py` | ย้ายไป `app/mock/data_source.py` |
| `module5_recommend/weather_provider.py` | ย้ายไป `app/mock/weather_provider.py` |
| `backend/seed_module5.py` (ตัวจริง) | เป็น shim แล้ว ตัวจริงอยู่ `app/mock/seed_module5.py` |

## หมายเหตุไฟล์ `__init__.py`

- `app/ai/__init__.py`, `module1_behavior` — ว่างเปล่า (ทำให้เป็น package)
- `module2_prediction/__init__.py`, `module3_risk/__init__.py`, `module4_search_area/__init__.py` — export ฟังก์ชัน/คลาสหลักของโมดูล
- `module5_recommend/__init__.py` — export ฟังก์ชันและคลาสของ Module 5 (จงใจไม่ import `ranker` เพื่อไม่ลาก sklearn เข้ามาทุกครั้ง)
- `db/__init__.py` — export ฟังก์ชัน database และโมเดล ORM
- `models/__init__.py` — export schema ทั้งหมดจากไฟล์ในโฟลเดอร์
- `mock/__init__.py` — มีแต่ docstring ที่เป็นกฎ: **ห้ามโค้ดบนเส้นทาง serving import จาก package นี้** มีไว้ป้อน offline evaluation, ชุดเทสต์ และการ seed เท่านั้น
- `api/__init__.py`, `services/__init__.py` — ว่างเปล่า
