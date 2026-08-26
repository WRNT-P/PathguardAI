# PathGuard AI — โครงสร้างโปรเจกต์ (backend/app)

โครงสร้างไฟล์จริงของ backend พร้อมคำอธิบายภาษาไทยทุกโฟลเดอร์และทุกไฟล์
(ชื่อไฟล์/โฟลเดอร์เป็นภาษาอังกฤษ คำอธิบายเป็นภาษาไทย)

> หมายเหตุ: โมดูล AI ครบทั้ง 1–5 มีโค้ดจริงแล้ว (รวม Module 4 Search Area และ learned ranker ของ Module 5)

```
backend/
├── main.py                          — ตัวเรียกใช้งานบาง ๆ ให้ `uvicorn main:app` ทำงานได้ (import app จาก app/main.py)
├── seed_module5.py                  — สคริปต์ใส่ข้อมูลจำลอง (โปรไฟล์ + สถานที่) เพื่อทดสอบ Module 5 ก่อนมี GPS จริง
├── requirements.txt                 — รายการ dependency หลัก (fastapi, sqlalchemy, scikit-learn, tensorflow ฯลฯ)
├── requirements-dev.txt             — dependency สำหรับทดสอบ (pytest, pytest-asyncio, aiosqlite)
│
├── scripts/                         — data pipeline: รันข้อมูลจริงผ่าน Module 1–5 (issue #1, ไม่แตะโค้ด AI)
│   ├── import_geolife.py            — โหลดข้อมูลจริง GeoLife (.plt) → derive speed/direction → remap เวลา → bulk-load เข้า gps_data
│   ├── inject_wandering.py          — เติมจุด pacing สังเคราะห์ + danger zone (synthetic_injected=True) ให้มีพฤติกรรมหลงทางตรวจจับ
│   ├── migrate_add_synthetic_injected.py — migration เพิ่มคอลัมน์ synthetic_injected (gps_data + danger_zones; ไม่มี alembic)
│   ├── demo_run.py                  — terminal demo: รัน Module 1–5 ต่อผู้ป่วย แล้ว print narrative (TF-free)
│   ├── demo_server.py               — เซิร์ฟเวอร์ dashboard ผู้ดูแล (mount router จริง + เสิร์ฟ dashboard.html)
│   ├── dashboard.html               — หน้า dashboard ภาษาไทย: แผนที่ Leaflet, เกจความเสี่ยง, ฟีดแจ้งเตือน
│   ├── measure_learning_days.py     — วัดว่าต้องเก็บ GPS กี่วันชุดสถานที่จึงนิ่ง (Phase 6) + prototype stay-point
│   └── README.md                    — ลำดับการรัน pipeline (migrate → seed → import → inject → demo)
│
└── app/
    ├── main.py                      — จุดเริ่มต้นแอป FastAPI: สร้าง init_db ตอน startup + mount router ทุกตัว
    │
    ├── ai/                          — โมดูล AI ทั้งหมด (สมองของระบบ ทำงานเรียงลำดับ 1→5)
    │   ├── lstm_utils.py            — โมเดล LSTM ที่ใช้ร่วมกัน ทำนายคลัสเตอร์ตำแหน่งถัดไปจากลำดับการเคลื่อนที่
    │   │
    │   ├── module1_behavior/        — เรียนรู้พฤติกรรม/กิจวัตรการเดินทางจากประวัติ GPS
    │   │   ├── data_collection.py        — (dead code ตั้งแต่ 19 ส.ค.) เก็บ GPS ลง list ในหน่วยความจำ ไม่มีใครเรียกแล้ว
    │   │   ├── known_places.py           — กฎการผสม known_places จากหมุดผู้ดูแล + ผลคลัสเตอร์ (merge + ปรับสเกล, 22 ส.ค.)
    │   │   ├── data_preprocessing.py     — ทำความสะอาดข้อมูล GPS + กรอง noise ด้วย Kalman ก่อนส่งให้ DBSCAN/LSTM
    │   │   ├── place_clustering.py       — จัดกลุ่มสถานที่ที่ไปบ่อยด้วย DBSCAN (haversine) + คำนวณเวลาพำนัก
    │   │   ├── sequence_learning.py      — ตัวห่อ LSTM สำหรับฝึกและทำนายสถานที่ถัดไปจากลำดับ
    │   │   └── behavior_pipeline.py      — ตัวเชื่อม DB↔AI: อ่านประวัติ GPS → preprocess → cluster → **merge กับหมุด** → เขียนโปรไฟล์ (ยังไม่มีใครตั้งเวลาเรียก)
    │   │
    │   ├── module2_prediction/      — ตรวจจับการเดินหลงทางและทำนายปลายทาง/เส้นทาง
    │   │   ├── cluster_matcher.py             — คำนวณระยะ Haversine และหาคลัสเตอร์ที่คุ้นเคยที่ใกล้ที่สุด
    │   │   ├── destination_prediction.py      — ใช้ LSTM ทำนายปลายทางจากประวัติ GPS และพฤติกรรม
    │   │   ├── route_prediction.py            — ใช้ HMM + Viterbi + DTW ทำนายเส้นทางจากตำแหน่งปัจจุบันไปปลายทาง
    │   │   ├── stop_confusion_classification.py — จำแนกการหยุดว่าปกติหรือสับสน ด้วย Gradient Boosting (5 features)
    │   │   └── wandering_detection.py         — ตรวจจับพฤติกรรมเดินวนหลงทางด้วย Isolation Forest (5 features)
    │   │
    │   ├── module3_risk/            — คำนวณคะแนนความเสี่ยง 0–100 และตัดสินใจแจ้งเหตุฉุกเฉิน
    │   │   ├── risk_data_collection.py     — รวบรวมปัจจัยเสี่ยง 5 ตัว โดยเรียกตัวตรวจจับของ Module 2
    │   │   ├── data_normalization.py       — ปรับค่าตัวแปรเสี่ยง 5 ตัวให้อยู่ในช่วง [0,1] ก่อนคิดคะแนน
    │   │   ├── risk_score_calculation.py   — คำนวณคะแนนเสี่ยง 0–100 จากน้ำหนัก 5 ตัวแปร (route .30/wandering .25/confusion .20/zone .15/unfamiliarity .10)
    │   │   ├── emergency_decision_engine.py — ตัดสินว่าต้องแจ้งฉุกเฉินหรือไม่ (กฎ danger_zone หรือ risk_score > 80)
    │   │   └── gps_failure_handling.py     — ตรวจจับช่องว่างสัญญาณ GPS ที่นานเกิน (>600 วิ) และเก็บตำแหน่งสุดท้าย
    │   │
    │   ├── module4_search_area/     — ประเมินพื้นที่ค้นหาจากตำแหน่งล่าสุดเมื่อ GPS หาย (ไฟล์ AI ไม่ import กันเอง — router search_area.py เป็นตัวร้อยเรียง)
    │   │   ├── last_known_position.py       — ดึงตำแหน่งล่าสุด + คำนวณรัศมีค้นหา (Distance = Speed × Time)
    │   │   ├── movement_path_simulation.py  — จำลองเส้นทางที่เป็นไปได้ด้วย Monte Carlo + A* อย่างง่าย (offline)
    │   │   ├── probability_area_estimation.py — ประเมินโซนความน่าจะเป็น High/Medium/Low ด้วย KDE (กริด 50×50)
    │   │   └── search_radius_adjustment.py  — ปรับรัศมีตาม wandering score + ความหนาแน่นของสถานที่คุ้นเคย (ไม่ใช่ระดับอาการสมองเสื่อม)
    │   │
    │   └── module5_recommend/       — สร้างและจัดลำดับคำแนะนำสถานที่ให้ผู้ดูแล (มี learned ranker)
    │       ├── user_context_analysis.py        — รวมโปรไฟล์พฤติกรรม + ตำแหน่งปัจจุบันเป็นออบเจ็กต์ UserContext
    │       ├── recommendation_generation.py    — ให้คะแนนสถานที่แบบกฎถ่วงน้ำหนัก หรือใช้ learned ranker ถ้ามีโมเดลฝึกไว้ (ติดธง scorer=ml/rules)
    │       ├── recommendation_prioritization.py — เรียงลำดับและคัดกรองสถานที่สูงสุด N อันดับตามคะแนนความมั่นใจ/ความถี่
    │       ├── ranker.py                        — Learned Pointwise Ranker (HistGradientBoosting) จัดอันดับสถานที่ + เก็บ .pkl ต่อผู้ป่วย
    │       ├── featurize.py                     — สร้าง 8 features คงที่ให้ ranker (slot/weekend/weather/distance/frequency/familiarity)
    │       ├── weather_provider.py              — ตัวจ่ายสภาพอากาศจำลอง (seeded) สำหรับฝึก/ทดสอบ
    │       ├── data_source.py                   — สร้างข้อมูลจำลอง (seeded) สำหรับฝึกและประเมิน ranker
    │       └── evaluation.py                    — ชุดวัดความซื่อสัตย์ของโมเดล (temporal split, bootstrap CI, baselines, go/no-go)
    │
    ├── api/                         — ชั้น endpoint ที่รับ request จากแอป Flutter
    │   ├── users.py                 — POST /api/register ลงทะเบียนผู้ใช้ใหม่จาก Firebase UID
    │   ├── gps.py                   — POST /api/gps + /api/gps/batch บันทึก GPS ผ่าน gps_processor แล้วสั่งคิด risk เอง (throttle 60 วิ)
    │   ├── prediction.py            — GET /api/predict-destination/{patient_id} (+ POST .../train) ทำนายปลายทาง
    │   ├── recommendation.py        — GET /api/recommendation/{patient_id} แนะนำสถานที่น่าจะไป 3 อันดับ
    │   ├── risk.py                  — GET /api/risk/{patient_id} คำนวณ บันทึก และแจ้งเตือนคะแนนความเสี่ยง
    │   ├── search_area.py           — GET /api/search-area/{patient_id} ประเมินพื้นที่ค้นหา (เรียก detect_gps_gap ของ Module 3, เขียน alert เมื่อ GPS หาย)
    │   ├── admin_rules.py           — GET /api/admin/rules (+/history) อ่านฐานความรู้กฎ + ประวัติการแก้
    │   ├── places.py                — POST/GET /api/patients/{id}/places หมุดสถานที่ของผู้ดูแล (รับ rank ไม่รับตัวเลข)
    │   ├── danger_zones.py          — POST/GET/DELETE /api/danger-zones เขตอันตราย (ใช้ร่วมทุกคนไข้)
    │   ├── devices.py               — POST /api/devices/token เก็บ FCM token ของเครื่องผู้ดูแล (22 ส.ค.)
    │   ├── tracking.py              — GET /api/patients/{id}/track เส้นทางล่าสุดให้แอปวาดแผนที่ (22 ส.ค.)
    │   └── alerts.py                — GET /api/patients/{id}/alerts + PATCH /api/alerts/{id} ปิดแจ้งเตือน (22 ส.ค.)
    │
    ├── db/                          — จัดการฐานข้อมูล PostgreSQL + Firebase
    │   ├── database.py              — ตั้งค่า engine PostgreSQL/asyncpg (รองรับ Neon) และเริ่ม Firebase Admin SDK
    │   ├── models.py                — โมเดล ORM (User, GPSData, RiskScore, Alert, BehavioralProfile, ตาราง KB 4 ตัว, DeviceToken, PushNotification)
    │   ├── rule_repository.py       — อ่าน/แก้ฐานความรู้กฎ (น้ำหนัก, threshold, เขตอันตราย, temporal) พร้อม audit log
    │   └── crud.py                  — ฟังก์ชันอ่าน/เขียน DB (สร้าง/ดึง user, ประวัติ GPS, risk score, alert, profile)
    │
    ├── models/                      — Pydantic schema สำหรับ validate request/response ของ API
    │   ├── user_profile.py          — schema: UserCreate, UserResponse, BehavioralProfileResponse
    │   ├── gps_data.py              — schema: GPSDataCreate, GPSDataResponse, LiveGPSUpdate
    │   ├── risk_score.py            — schema: RiskScoreCreate, RiskScoreResponse
    │   ├── alert.py                 — schema: AlertCreate, AlertResponse, AlertResolve
    │   ├── prediction.py            — schema: PredictionResponse, TopPrediction
    │   └── recommendation.py        — schema: RecommendationResponse, RecommendedPlace, RecommendationFlags
    │
    └── services/                    — บริการพื้นฐานที่โมดูลอื่นเรียกใช้ (GPS smoothing, Firebase, แจ้งเตือน)
        ├── kalman_filter.py         — Kalman filter 2D แบบสด (ทีละจุด) ติดตาม position + velocity ลด noise GPS
        ├── kalman_batch.py          — Kalman filter 1D แบบ batch ย้อนหลัง + adaptive jump detection
        ├── gps_processor.py         — orchestrator รับ GPS: smooth → บันทึก PostgreSQL → ส่ง live update ขึ้น Firebase
        ├── firebase.py              — update_live_position เขียนตำแหน่งสดขึ้น Firebase Realtime DB
        ├── notification.py          — ยิง FCM ถึงผู้ดูแล + cooldown 10 นาที (เขียน 22 ส.ค.)
        └── auth.py                  — ตรวจ Firebase ID token + สิทธิ์เข้าถึงคนไข้ (22 ส.ค., ปิดสวิตช์อยู่)
```

## หมายเหตุไฟล์ `__init__.py`

- `app/ai/__init__.py`, `module1_behavior` — ว่างเปล่า (ทำให้เป็น package)
- `module2_prediction/__init__.py`, `module3_risk/__init__.py`, `module4_search_area/__init__.py` — export ฟังก์ชัน/คลาสหลักของโมดูล
- `module5_recommend/__init__.py` — export ฟังก์ชันและคลาสของ Module 5 (จงใจไม่ import `ranker` เพื่อไม่ลาก sklearn เข้ามาทุกครั้ง)
- `db/__init__.py` — export ฟังก์ชัน database และโมเดล ORM
- `models/__init__.py` — export schema ทั้งหมดจากไฟล์ในโฟลเดอร์
- `api/__init__.py`, `services/__init__.py` — ว่างเปล่า
