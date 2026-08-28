# PathGuard AI — การไหลของข้อมูล (Data Flow) ครบทั้ง 5 โมดูล

> ✅ **ไล่กับโค้ดจริงเมื่อ 27 ส.ค. 2026** เดิมเขียน 22 ส.ค. และคลาดเคลื่อนสองแบบ ไม่ใช่แบบเดียว
> **ที่ขาด** (เพิ่มแล้ว): `POST /api/sos` · การจับคู่เครื่อง · การขออนุมัติเดินทาง C-3 ·
> `users.severity_level` · `routine_patterns` และปัจจัยช่วงเวลาของ Module 5 · หมุดสถานที่
> **ที่เขียนผิดไปแล้ว** (แก้แล้ว): บอกว่าน้ำหนักคะแนนเสี่ยงฝังในโค้ด · บอกว่าเกณฑ์ฉุกเฉินคือ
> `> 80` ตายตัว · บอกว่าการปรับรัศมีค้นหาคูณทบกันทุกทาง · ยังนับ `sequence_learning.py`
> ที่ถูกลบไปแล้ว
> ♻️ **ไล่ซ้ำ 28 ส.ค. 2026** หลังงานผู้ดูแลหลายคนขึ้น main: แก้ `get_caregiver_id` → `get_caregiver_ids`
> ที่ `services/auth.py` ใช้จริง · เพิ่ม `link_caregiver` · รหัสเชิญ · `update_user_location`
> · แก้จำนวนรายการแนะนำเป็น 3 ทุกระดับ
> **สายที่ครบที่สุดสำหรับฝั่งแอปคือ `backend/API_CONTRACT_APP.md`** ส่วนตารางดูที่ `database_layer.md`

ติดตามเส้นทางข้อมูลจริงจาก `import` และการเรียกฟังก์ชันในโค้ด (ไม่ใช่การเดา)
รูปแบบบรรทัด: `filename [แท็ก AI/ML] — รับ: <input จากไหน> → ทำ: <สิ่งที่ทำ> → ส่ง: <output ไปไหน>`
(ชื่อไฟล์/ฟังก์ชันเป็นภาษาอังกฤษ คำอธิบายเป็นภาษาไทย)

> อัปเดตล่าสุด: **2026-08-22** (ของเดิม 2026-07-05) — แก้ส่วนที่ล้าสมัยหลังงาน
> 19–22 ส.ค. เอกสารนี้เล่า *การไหลของข้อมูลในโมดูล AI* เป็นหลัก ส่วนสถานะงาน/
> สิ่งที่เหลือ ให้ดู `CLAUDE.md` และ `docs/plan_person2_lane.md` ซึ่งเป็นตัวจริง

## แท็กประเภทของแต่ละไฟล์ (ตอบคำถาม "ไฟล์ไหนเป็น AI/ML")

| แท็ก | ความหมาย |
|---|---|
| 🟢 **Learned ML** | มีโมเดลที่ "ฝึก/โหลด" จริง (มี weights หรือ artifact) — LSTM, DBSCAN, IsolationForest, GradientBoosting ranker |
| 🔵 **Statistical/Algorithmic** | คณิต/สถิติจริง แต่ไม่ใช่โมเดลที่ฝึก — Kalman, HMM+Viterbi, Monte Carlo, KDE, สูตรถ่วงน้ำหนัก |
| 🟡 **Heuristic/Rules** | กฎ if/else + เกณฑ์ที่ตั้งด้วยมือ |
| ⚪ **Plumbing/Data** | เก็บ/เตรียม/ส่งผ่านข้อมูล ไม่มีการโมเดล |

**สรุปไฟล์ที่เป็น "โมเดลที่ฝึกจริง" (🟢) มี 5 ไฟล์:** `lstm_utils.py`, `place_clustering.py`, `destination_prediction.py`, `wandering_detection.py`, `ranker.py` — คิดเป็นโมเดลที่ต่างกัน 4 ตัว: LSTM (ลำดับสถานที่), DBSCAN (จับกลุ่ม), IsolationForest (จับความผิดปกติ), Gradient-Boosted ranker (จัดอันดับคำแนะนำ)

> **แก้ 27 ส.ค.: `sequence_learning.py` ถูกลบไปแล้ว** พร้อมการตัด TensorFlow — เอกสารฉบับก่อนยังนับมันอยู่
> และในสองตัวที่เหลือ **`lstm_utils.py` กับ `destination_prediction.py` ไม่ได้ถูกเรียกใน production**
> (`api/prediction.py` ไม่ได้ mount) ส่วน `place_clustering.py` ไม่ถูกเรียกเพราะ **ตัดสินใจไม่ใช้**
> เหลือที่ทำงานจริงบนเส้นทาง serving คือ `wandering_detection.py` ตัวเดียว และ `ranker.py`
> เฉพาะเมื่อมีไฟล์โมเดลของผู้ป่วยคนนั้น

---

> **ภาพรวมเส้นเลือดหลักของระบบ**
> GPS ดิบ → (smooth ด้วย Kalman + เก็บลง PostgreSQL) → **Module 1** เรียนรู้ "สถานที่คุ้นเคย" เก็บใน `behavioral_profile.known_places` → **Module 2/3/4/5** อ่าน profile + ประวัติ GPS เดียวกันไปทำนาย/ให้คะแนนเสี่ยง/ประเมินพื้นที่ค้นหา/แนะนำสถานที่
> **หัวใจการเชื่อมต่อ:** ทุกโมดูลอ่านผลของ Module 1 ผ่าน `crud.get_behavioral_profile()` และอ่านประวัติผ่าน `crud.get_gps_history()` / `crud.get_latest_gps()`
> **จุดต่อสำคัญข้ามโมดูล:** Module 3 เรียก detector ของ Module 2 โดยตรง ; Module 4 เรียก `detect_gps_gap()` ของ Module 3 เพื่อเช็กว่าผู้ป่วยหายจริงไหม

---

## ⚠️ หมายเหตุ: มี GPS ingestion สองเส้นทางในโค้ด

> **แก้แล้ว 2026-08-19** — เดิมหัวข้อนี้เขียนว่ามีสามเส้นทาง และเส้นที่ต่อกับ router
> เก็บลง list ในหน่วยความจำ (`gps_storage`) ไม่ลง DB จริง **ไม่จริงแล้ว**
> `api/gps.py` เรียก `gps_processor.process_gps_point` ตรงๆ · `save_gps_data`
> ยังอยู่ในไฟล์แต่ไม่มีใครเรียก (dead code ที่ตั้งใจทิ้งไว้ ไม่ใช่เส้นทางที่ใช้งาน)

1. **เส้นหลัก (ใช้จริง)** — `POST /api/gps` และ `POST /api/gps/batch` (`api/gps.py`)
   เรียก `services/gps_processor.process_gps_point` → Kalman smooth → เขียน
   PostgreSQL → push Firebase · แล้วสั่งคำนวณ risk ต่อทันที (throttle 1 ครั้ง/60 วิ/คนไข้)
   · `batch` เรียงตาม `recorded_at` ก่อนป้อน Kalman เพราะฟิลเตอร์เป็น state ต่อเนื่อง
2. **เส้น bulk-load ข้อมูลจริง (ใช้จริงแล้ว, issue #1)** — `scripts/import_geolife.py` โหลดข้อมูลจริง **Microsoft GeoLife** เข้า `gps_data` โดยตรง (derive speed/direction, remap เวลาให้เป็นปัจจุบัน, downsample) ; `scripts/inject_wandering.py` เติมจุด pacing สังเคราะห์ (`synthetic_injected=True`) เพื่อให้มีพฤติกรรมหลงทางให้ตรวจจับ — พิสูจน์ว่า Module 1–5 รันบนข้อมูลจริงได้ (ดู `scripts/README.md`, `scripts/demo_run.py`)

Module 2/3/4/5 อ่านข้อมูลจาก **PostgreSQL** (`GPSData`, `BehavioralProfile`) ผ่าน `crud` — ต้องมีข้อมูลถูกเขียนผ่าน `gps_processor` / `behavior_pipeline` ก่อน endpoint เหล่านี้จึงจะได้ข้อมูลจริง

---

## Module 1 — Behavior (เรียนรู้สถานที่/กิจวัตร)

**ระดับโมดูล**
- **เข้า:** GPS ดิบจากแอป (ผ่าน `gps_processor`) → PostgreSQL ; การฝึกเรียกด้วย `analyze_behavior(patient_id)`
- **ออก:** สถานที่ที่เรียนรู้ (clusters) → เขียนลง `behavioral_profile.known_places` (JSON) ผ่าน `crud.upsert_behavioral_profile()` → กลายเป็น input ของ Module 2/3/4/5

**ระดับไฟล์**
- `gps_processor.py` ⚪ — รับ: `GPSDataCreate` (GPS ดิบจากมือถือ) → ทำ: `kalman_filter.smooth()` ลด noise → `crud.save_gps_point()` เขียน PostgreSQL → `firebase.update_live_position()` push ตำแหน่งสด → ส่ง: แถว `GPSData` + ตำแหน่งสดขึ้น Firebase
- `kalman_filter.py` 🔵 — รับ: `(lat, lng)` ทีละจุด → ทำ: Kalman 2D แบบ stateful (1 filter ต่อ patient_id) ติดตาม position+velocity → ส่ง: `(smooth_lat, smooth_lng)`
- `behavior_pipeline.py` ⚪ — รับ: `(db, patient_id, days=30)` → ทำ: `crud.get_gps_history()` → `gps_history_to_dataframe()` → `preprocess_gps()` → `cluster_places()` → ส่ง: เขียน places ลง DB ด้วย `crud.upsert_behavioral_profile(known_places=json.dumps(places), last_trained_at=...)`
- `data_preprocessing.py` 🔵 — รับ: `DataFrame(lat, lng, speed, timestamp)` → ทำ: ลบ NaN + `KalmanFilter.smooth()` (batch จาก `kalman_batch`) + moving average ความเร็ว → ส่ง: DataFrame สะอาดให้ `cluster_places`
- `kalman_batch.py` 🔵 — รับ: `numpy arrays (latitudes[], longitudes[])` → ทำ: Kalman 1D ต่อแกน + adaptive jump detection → ส่ง: arrays ที่ smooth แล้ว
- `place_clustering.py` 🟢 — รับ: DataFrame สะอาด → ทำ: **DBSCAN** (eps≈50m, min_samples=5, haversine) + คำนวณเวลาพำนักเฉลี่ยต่อ visit → ส่ง: `list[dict]` `{cluster_id, latitude, longitude, visit_frequency, avg_stay_time}` — **ไม่มี `place_name` เพราะอัลกอริทึมตั้งชื่อสถานที่ไม่ได้**
  ⚠️ **วัดแล้ว 22 ส.ค.: ได้ 156 "สถานที่" ใน 30 วัน 124 ตัวมีเวลาพำนักเฉลี่ยไม่ถึง 5 นาที ก้อนใหญ่สุดกว้าง 1,533 ม. จาก centroid ตัวเอง** มันไม่ได้หาสถานที่ มันหา*ช่วงที่จุด GPS หนาแน่น* ไฟแดงกลายเป็นจุดหมาย ถนนกลายเป็นก้อนเดียวยาว 1.5 กม.
  **ตัดสินใจ 26 ส.ค. ว่าไม่ใช้ (WONTFIX)** production ใช้หมุดที่ผู้ดูแลปัก โค้ดเก็บไว้เป็นผลการทดลองในรายงาน
- `routine_patterns.py` 🔵 — **(เพิ่ม 26 ส.ค.)** รับ: `build_routine_patterns(gps_history, known_places)` → ทำ: นับว่าแต่ละชั่วโมงท้องถิ่นผู้ป่วยอยู่ที่หมุดไหนบ่อยแค่ไหน (แปลงเวลาด้วย `local_hour()` ตัวเดียวที่ทุกฝั่งใช้ร่วมกัน) → ส่ง: `[{hour, cluster_id, probability}]` เขียนลง `behavioral_profiles.routine_patterns` ผ่าน `scripts/build_routine_patterns.py` — **นี่ไม่ใช่การหาสถานที่ สถานที่มาจากหมุด อันนี้เรียนแค่ *ชั่วโมง*** ไม่มีหมุดก็เรียนอะไรไม่ได้
- `lstm_utils.py` 🟢 — รับ: sequence features `[cluster_id, hour, day, familiarity]` → ทำ: สร้าง/ฝึก/ทำนายด้วย **LSTM (TensorFlow/Keras)** เก็บ weights `.h5` ต่อ patient → ส่ง: `(cluster_id, confidence, probs)` (ใช้ร่วมกับ Module 2)
- `data_collection.py` ⚪ — **ไม่มีใครเรียกแล้วตั้งแต่ 19 ส.ค.** (`api/gps.py` ใช้ `gps_processor` แทน) ฟังก์ชัน `save_gps_data()` เก็บลง list `gps_storage` ในหน่วยความจำ · ทิ้งไว้เฉยๆ ไม่ได้ลบ
- `firebase.py` ⚪ — รับ: `LiveGPSUpdate` → ทำ: เขียน `live_positions/{patient_id}` → ส่ง: ตำแหน่งสดขึ้น Firebase Realtime DB

---

## Module 2 — Prediction (ทำนายปลายทาง/เส้นทาง + ตรวจหลงทาง)

**ระดับโมดูล**
- **เข้า:** `patient_id` + ตำแหน่งปัจจุบัน (จาก `api/prediction.py`) ; `known_places` + ประวัติ `GPSData` (จาก DB ผ่าน `crud`)
- **ออก:** `PredictionResponse` (cluster ปัจจุบัน/ปลายทาง, confidence, top 3) ให้ frontend ; **และ detector ทั้งสาม (Wandering/Confusion/Route) ถูก Module 3 เรียกใช้โดยตรง** เพื่อคิดคะแนนเสี่ยง

**ระดับไฟล์**
- `cluster_matcher.py` 🔵 — รับ: `(lat, lng, known_places)` → ทำ: `haversine_km()`, `find_nearest_cluster()` จับคู่สถานที่ที่จุดนั้นตกอยู่ข้างใน **โดยเทียบกับ `radius_m` ของแต่ละหมุดเอง** (ไม่มีค่าใช้ 150 m เหมือนเดิม — แก้ 20 ส.ค.) แล้วค่อยเลือกตัวใกล้สุด, `get_familiarity()` → ส่ง: `cluster_id` / `familiarity (0–1)` (Module 3 import ตรง ๆ)
- `destination_prediction.py` 🟢 — รับ: `(db, patient_id, current_lat, current_lng)` → ทำ: `crud.get_behavioral_profile()` + `crud.get_gps_history()` → แปลง GPS เป็น cluster sequence → **LSTM** (`SequencePredictor`) ทำนาย → ส่ง: `{predicted_destination_cluster_id, confidence, top_predictions}`
- `route_prediction.py` 🔵 — รับ: `fit(gps_records, known_places)` แล้ว `predict_route(recent_gps, dest_id, known_places)` → ทำ: **HMM** transition matrix → **Viterbi** decode → ต่อเส้นทางแบบ greedy → **DTW** เทียบเส้นทางอดีต → ส่ง: `{predicted_route:[{lat,lng,cluster_id}], similarity_score, route_familiar}` (Module 3 ใช้ waypoints คิด route deviation)
- `stop_confusion_classification.py` 🟡 — รับ: `classify(recent_gps, stop_duration, lat, lng, predicted_route, known_places)` → ทำ: สกัด 5 features → คะแนนแบบ rule-based (`_rule_based_score`, มี `fit_synthetic()` ทำ label จำลอง) → ส่ง: `{status: normal|confused, confidence_score (0–1)}` (Module 3 ใช้เป็นค่า confusion)
- `wandering_detection.py` 🟢 — รับ: `fit(gps_30d, known_places)` แล้ว `detect(recent_gps)` → ทำ: sliding-window 5 features → **IsolationForest** anomaly score → map เป็น 0–1 (fallback เป็น rule เมื่อยังไม่ fit) → ส่ง: `{wandering_detected, wandering_score (0–1), wandering_level}` (MILD=0.55, HIGH=0.75 ; Module 3 ใช้ `wandering_score`)
- `__init__.py` ⚪ — export `find_nearest_cluster/get_familiarity/haversine_km` ทันที + lazy-import `DestinationPredictor` (เลี่ยงดึง TensorFlow) ; `WanderingDetector/StopConfusionClassifier/RoutePredictor` import จากไฟล์ตรง ๆ
- `prediction.py` (API) ⚪ — รับ: `GET /api/predict-destination/{patient_id}?lat&lng` (+ `POST .../train`) → ทำ: `_get_predictor()` → `predictor.predict(db, ...)` → ส่ง: `PredictionResponse`

---

## Module 3 — Risk (ให้คะแนนเสี่ยง 0–100 + ตัดสินใจฉุกเฉิน)

**ระดับโมดูล**
- **เข้า:** `patient_id` + lat/lng (จาก `api/risk.py`) ; `BehavioralProfile` + ประวัติ GPS 30 วัน + GPS ล่าสุด ; **ผลจาก Module 2 detectors** (fit ใหม่ทุก request)
- **ออก:** เขียน `RiskScore` + (ถ้าจำเป็น) `Alert` ลง DB ผ่าน `crud` ; ส่ง `RiskResponse` กลับ caller

**🔗 risk_data_collection.py เรียก Module 2 อย่างไร (ยืนยันในโค้ด)**
```python
from app.ai.module2_prediction.wandering_detection import WanderingDetector
from app.ai.module2_prediction.stop_confusion_classification import StopConfusionClassifier
from app.ai.module2_prediction.route_prediction import RoutePredictor
from app.ai.module2_prediction.cluster_matcher import haversine_km, get_familiarity, find_nearest_cluster
```
- `WanderingDetector().fit(gps_30d, known_places).detect(recent_gps)` → W (`wandering_score`)
- `StopConfusionClassifier().fit_synthetic().classify(...)` → C (เฉพาะเมื่อ "หยุดนิ่ง" avg speed < 0.3 m/s)
- `RoutePredictor().fit(gps_30d, known_places).predict_route(...)` → waypoints → คิดระยะเบี่ยงเส้นทาง D
- `find_nearest_cluster()` + `get_familiarity()` → familiarity (ดิบ) ; `haversine_km()` ใช้ใน `is_in_danger_zone()` เทียบเขตอันตราย **ที่โหลดจากตาราง `danger_zones` ใน KB** (เดิม hardcoded ในไฟล์ ย้ายเข้า DB ตอน refactor 9 ก.ค.)

**ระดับไฟล์**
- `risk_data_collection.py` ⚪ (orchestrator) — รับ: `(gps_30d, recent_gps, profile_dict, lat, lng)` (ดึงมาแล้ว ไม่แตะ DB) → ทำ: fit + เรียก Module 2 detectors, คิด 5 ปัจจัยดิบ + danger zone (safety-biased defaults) → ส่ง: `{route_deviation, wandering, confusion, danger_zone, familiarity, _meta}`
- `data_normalization.py` ⚪ — รับ: ปัจจัยดิบ → ทำ: `normalize_route_deviation()` (m/500), `scale_wandering()`, `convert_boolean()`, `compute_unfamiliarity()` (1−familiarity) clamp [0,1] → ส่ง: 5 ค่าปกติ D,W,C,Z,F
- `risk_score_calculation.py` 🔵 — รับ: `calculate_risk(factors, weights, low_ceiling=, medium_ceiling=)` → ทำ: ถ่วงน้ำหนักผลรวม → ส่ง: `{risk_score (0–100), risk_level, contributions}`
  ⚠️ **แก้ 27 ส.ค.: น้ำหนักไม่ได้ฝังในโค้ด** เอกสารฉบับก่อนเขียนว่าเป็น `0.30*D + 0.25*W + …` ตายตัว
  ความจริงคือ `weights` กับเส้นแบ่งระดับถูก**ส่งเข้ามาจากฐานความรู้** (`risk_factor_weights`, `risk_thresholds`)
  ค่าที่ seed ไว้บังเอิญเป็น 0.30/0.25/0.20/0.15/0.10 และ `api/risk.py` โหลดใหม่ทุก request ไม่มี cache
  **แต่ระวัง: วันนี้ยังไม่มี endpoint ไหนแก้ค่าพวกนี้ได้** ดู `database_layer.md` หัวข้อ 2.2
  โหมด partial (ผู้ป่วยที่ยังไม่มีหมุด) เกลี่ยน้ำหนักใหม่**จากค่าใน KB ตอน runtime** ห้ามฮาร์ดโค้ด 0.625/0.375
- `gps_failure_handling.py` 🟡 — รับ: `(last_reading, now, threshold_s=600.0)` → ทำ: คิดช่องว่างเวลาเทียบเกณฑ์ 600 วิ (unknown ⇒ ถือว่า GPS หาย = ปลอดภัยไว้ก่อน) → ส่ง: `{gps_lost, gap_seconds, last_known}` — **dict นี้คือ handoff contract ที่ Module 4 บริโภค (ตั้งใจไม่เรียก Module 4 ตรง ๆ)**
- `temporal_adjustment.py` 🟡 — **(ไม่มีในเอกสารฉบับก่อน)** รับ: `(current_score, recent_scores, rules)` → ทำ: กฎที่อ่าน *ประวัติ* ไม่ใช่ค่าเดียว — `trend_escalation` (คะแนนไต่ขึ้นต่อเนื่อง → บวกเพิ่ม) และ `sustained_high_risk` (สูงติดกันหลายรอบ → ยกระดับเป็นฉุกเฉิน) → ส่ง: `{adjusted_score, triggered:[ชื่อกฎ]}` **เป็นฟังก์ชันบริสุทธิ์ ตัวเลขทั้งหมดมาจาก `temporal_rules.parameters`**
- `emergency_decision_engine.py` 🟡 — รับ: `decide_emergency(risk_score, danger_zone, emergency_score)` → ทำ: เขตอันตรายมาก่อนเสมอ แล้วจึง `risk_score > emergency_score` (เทียบแบบ `>` ไม่ใช่ `>=` — คะแนนเท่าเกณฑ์พอดีไม่ยิง) → ส่ง: `{emergency, reason, severity, alert_type}` (ไม่แตะ DB)
  ⚠️ **แก้ 27 ส.ค.: `emergency_score` ไม่ใช่ 80 ตายตัวในโค้ด** มันมาจาก `risk_thresholds` เอกสารฉบับก่อนเขียนว่า `risk_score > 80`
- `__init__.py` ⚪ — export `collect_risk_factors`, normalizers, `calculate_risk`, `detect_gps_gap`, `decide_emergency`
- `risk.py` (API — ไฟล์เดียวของ Module 3 ที่แตะ DB) ⚪ — รับ: `GET /api/risk/{patient_id}?lat&lng` → orchestrate ทั้ง pipeline → ส่ง: `RiskResponse` + เขียน `RiskScore`/`Alert`

**🔗 เส้นทางเต็มของ `GET /api/risk/{patient_id}`**
0. **`rule_repository` โหลดกฎสด** — น้ำหนัก, เกณฑ์, เขตอันตราย, กฎเชิงเวลา (ไม่มี cache: แก้กฎแล้วมีผลกับ request ถัดไปทันที)
1. `crud.get_behavioral_profile` → profile ; `crud.get_gps_history(days=30)` → `gps_30d` ; `recent_gps = gps_30d[-30:]` ; `crud.get_latest_gps` → `latest`
2. `collect_risk_factors(...)` → fit + detect (W, D, C, familiarity, Z)
3. normalize → `calculate_risk(normalized, weights, low_ceiling=, medium_ceiling=)` → `{risk_score, risk_level, contributions}`
   **ไม่มีหมุดเลย → โหมด partial**: เหลือ `wandering` + `danger_zone` แล้วเกลี่ยน้ำหนักใหม่จาก KB ติดธง `status:"partial"` + `factors_used`
4. **`temporal_adjustment`** อ่าน `get_recent_risk_scores` → บวก/ยกระดับตามกฎ → `risk_score` สุดท้าย (`contributions` ยังรวมได้เท่า `base_risk_score` ไม่ใช่ค่าสุดท้าย)
5. `detect_gps_gap(latest, now)` → `gps_available`
6. `save_risk_score(...)` เขียน `RiskScore`
7. `decide_emergency(..., emergency_score)` → ถ้า emergency → `save_alert(...)` → **`notify_alert()` ยิง push** ; ถ้า gps_lost → `save_alert(gps_loss, high)` → `notify_alert()`
8. คืน `RiskResponse`

> ⚠️ **นี่คือ `GET` ที่เขียนฐานข้อมูลและยิงการแจ้งเตือนเข้ามือถือคน** — ห้าม poll
> ปกติแอปไม่ต้องเรียกเลย เพราะ `POST /api/gps` สั่งให้คิดเองอยู่แล้วทุก 60 วินาที
> เขียนไว้ในสัญญาแล้วที่ `API_CONTRACT_APP.md` หัวข้อ 11.3

---

## Module 4 — Search Area (ประเมินพื้นที่ค้นหาจากตำแหน่งสุดท้าย)

**ระดับโมดูล**
- **เข้า:** `patient_id` + (last_lat/last_lng/last_speed/direction/time_missing — override ได้ผ่าน query) จาก `api/search_area.py` ; `known_places` (Module 1) + GPS ล่าสุด + ประวัติ 30 วัน (จาก DB) ; **เรียก `detect_gps_gap()` ของ Module 3** เพื่อยืนยันว่าผู้ป่วยหายจริง ; `wandering_score` ของ Module 2 ยังส่งเป็น `None` (ยังไม่ต่อสาย)
- **ออก:** `SearchAreaResponse` ให้ frontend ; เขียน `Alert (gps_lost, high)` เฉพาะเมื่อ `gps_lost=True`
- **หมายเหตุสถาปัตยกรรม:** 4 ไฟล์ AI **ไม่ import กันเอง** — ตัว orchestrator จริงคือ router `api/search_area.py :: get_search_area()` ที่ร้อยเรียงลำดับให้ ; ภายในแพ็กเกจมีแค่ `probability_area_estimation` เรียก `identify_target_locations` ของตัวเอง

**ระดับไฟล์**
- `last_known_position.py` 🔵 — รับ: `calculate_search_radius(last_speed_ms, time_missing_minutes)` + `extract_last_known(last_gps_record)` → ทำ: รัศมี = `speed * minutes * 60` เมตร (Distance = Speed × Time), speed default `1.4 m/s` เมื่อว่าง ; `extract_last_known` เลือก `smooth_*` (Kalman) ก่อน raw → ส่ง: `radius (float)` และ dict `{lat, lng, speed_ms, direction_deg, recorded_at, _meta}`
- `search_radius_adjustment.py` 🟡 — รับ: `adjust_radius(base_radius_m, known_places, origin_lat, origin_lng, wandering_score=None, severity_level=None)` → ทำ: ไม่มีที่คุ้นเคยในรัศมี ×**1.50** ; `wandering_score ≥ 0.75` ×**1.30** ; `≤ 0.30` ×**0.80** ; **ระยะกลาง (level 2) ×0.80 · ระยะต้น (level 1) ×1.20 · ไม่ระบุ = ไม่ปรับ** → ส่ง: `{adjusted_radius_m, adjustment_reason, _meta}`
  ⚠️ **แก้ 27 ส.ค.: การหด "ไม่ทบกัน"** เอกสารฉบับก่อนเขียนว่าคูณทบกันทุกทาง ซึ่งเลิกจริงตั้งแต่ 26 ส.ค.
  เหตุผล: การหดจาก wandering ต่ำถูกอธิบายไว้เองว่าเป็น*ตัวแทนของระยะกลาง* ถ้าคูณกันจะได้ 0.64
  คือ**หดพื้นที่ค้นหาผู้ป่วยที่หายไป 36% จากสัญญาณเดียวที่ถูกนับสองครั้ง** ระบบจึงเลือก
  **การหดที่น้อยที่สุดอันเดียว** ส่วนการขยายยังทบกันตามเดิม เพราะพื้นที่ใหญ่เกินคือทางที่ปลอดภัยกว่า
- `movement_path_simulation.py` 🔵 — รับ: `PathSimulator(n_simulations=10_000).fit(gps_30d).simulate_paths(last_lat, last_lng, direction, radius, known_places)` → ทำ: **Monte Carlo** — 70% เอียงตาม bearing ในอดีต ±45°, 30% สุ่มทั่ว, ระยะ `r = radius·√U` (กระจายเต็มพื้นที่วง) + **A\* อย่างง่าย** ลากเส้น great-circle 10 จุดไปยัง top-5 ที่คุ้นในรัศมี → ส่ง: `{status, endpoints:[[lat,lng]], familiar_paths:[...], _meta}`
- `probability_area_estimation.py` 🔵 — รับ: `estimate_probability_zones(endpoints, known_places, radius, origin_lat, origin_lng)` (+ public `identify_target_locations`) → ทำ: กริด 50×50 (2500 เซลล์) + **KDE** (`scipy.stats.gaussian_kde`) บน endpoints → normalize 0–1 → แบ่งโซนตาม percentile **High ≥ p70, Medium p40–p70, Low < p40** (ถ้า scipy ไม่มี/เสื่อม fallback แบ่งตามระยะเป็น 3 ชั้น) → ส่ง: `{high_zone/medium_zone/low_zone:[{lat,lng,probability}], target_locations, grid_bounds, _meta}`
- `__init__.py` ⚪ — export `calculate_search_radius, extract_last_known, PathSimulator, estimate_probability_zones, identify_target_locations, adjust_radius`
- `search_area.py` (API) ⚪ — รับ: `GET /api/search-area/{patient_id}?...` → ทำ: อ่าน DB → `extract_last_known` → `detect_gps_gap` (M3) → `calculate_search_radius` → `adjust_radius` → `PathSimulator.fit().simulate_paths()` → `estimate_probability_zones` → ส่ง: `SearchAreaResponse` (+ alert ถ้าหาย)

**🔗 ลำดับเรียกใน `get_search_area()`**
`extract_last_known` → `detect_gps_gap` (Module 3) → `calculate_search_radius` → `adjust_radius` → `PathSimulator.fit → simulate_paths` → `estimate_probability_zones (→ identify_target_locations)`

---

## Module 5 — Recommend (แนะนำสถานที่น่าจะไป) — runtime อ่านอย่างเดียว ไม่เขียน DB

โมดูลนี้มี **2 ชั้น**: (ก) **Runtime/API** ใช้กฎถ่วงน้ำหนัก และถ้ามีโมเดลที่ฝึกไว้ก็เสียบ learned ranker แทน ; (ข) **Offline train/eval** สร้างข้อมูลจำลอง ฝึก ranker แล้ว pickle เก็บไว้ให้ runtime โหลด

**ระดับโมดูล**
- **เข้า (runtime):** `patient_id` + lat/lng (จาก `api/recommendation.py`) ; `known_places` (Module 1) + GPS ล่าสุด ; โมเดลต่อผู้ป่วยจากไฟล์ `ranker_patient_{id}.pkl` (ถ้ามี)
- **ออก (runtime):** `RecommendationResponse` — **3 รายการทุกระดับ** (`_TOP_N_BY_LEVEL = {1: 3, 2: 3}` — แก้ 28 ส.ค. ตามที่ฝั่งแอปขอ ก่อนหน้านั้น Level 2 ได้ 5 ตามรายงาน) แต่ละรายการมี `place_name` ด้วยตั้งแต่ 27 ส.ค. — **ไม่เขียน DB**
- **เข้า (offline):** **ข้อมูลจำลอง** จาก `MockDataSource`/`MockWeather` — **ย้ายไป `app/mock/` แล้ว** ไม่ได้อยู่ใน `module5_recommend/` อีกต่อไป (`app/mock/__init__.py` เขียนกฎไว้ว่าโค้ดบนเส้นทาง serving ห้าม import จาก package นี้) + คลัสเตอร์จาก Module 1 บนหน้าต่างฝึก
- **ออก (offline):** artifact `ranker_patient_{id}.pkl` + รายงาน go/no-go

**ระดับไฟล์ (runtime path)**
- `user_context_analysis.py` ⚪ — รับ: `build_user_context(profile, lat, lng, now)` (profile = ORM ของ Module 1 หรือ None) → ทำ: `_parse_known_places()` แปลง `.known_places` JSON เป็น `list[dict]` (ทน error, คืน `[]`), decode `routine_patterns`, ตั้ง flag `has_profile` / `has_location` / **`has_routine`** → ส่ง: dataclass `UserContext`
  `has_routine` ต้องจริงสองอย่างพร้อมกัน: มีกิจวัตรในฐานข้อมูล **และ** ผู้เรียกมีนาฬิกามาให้ (`trip_confidence` ให้คะแนนปลายทางโดยไม่มีเวลาก็ได้ ตัวที่อ่านค่านี้จึงต้องรับมือกับ `now=None`)
- `recommendation_generation.py` 🔵 — รับ: `generate_recommendations(ctx, ranker=None)` / `score_place(place, ctx, ml_score=None)` → ทำ: ผสมกฎโปร่งใส `WEIGHTS {frequency:0.45, proximity:0.35, familiarity:0.20, time_match:0.25}` (proximity = `1/(1+haversine)` เมื่อมีตำแหน่ง) ; **เกลี่ยน้ำหนักเฉพาะปัจจัยที่มีข้อมูลจริงในรอบนั้น** ปัจจัยที่ไม่มีข้อมูลจึงไม่ได้ลากคะแนนลง มันแค่ไม่ได้โหวต ; **ถ้าส่ง `ranker` มา จะใช้ `ranker.score_places(...)` เป็น confidence แล้วติดธง `scorer="ml"`** (ไม่มีก็ `scorer="rules"` ไม่แอบอ้างว่าเป็น ML) → ส่ง: `list[ScoredPlace]` (มี `place_name` ตั้งแต่ 27 ส.ค.)
  ⚠️ **แก้ 27 ส.ค.: `time_match` ไม่ใช่ stub อีกแล้ว** น้ำหนักเคยเป็น 0.0 เพราะไม่มีใครเขียน `routine_patterns` — ตั้งแต่ 26 ส.ค. มีผู้เขียนและได้น้ำหนัก 0.25 ที่ต่ำกว่า `frequency` ตั้งใจ: "ปกติชั่วโมงนี้อยู่วัด" มีน้ำหนักน้อยกว่า "ไปวัดตลอด" เพราะกิจวัตรอนุมานมาจากประวัติเท่าที่มี ส่วนความถี่มาจากผู้ดูแล
- `recommendation_prioritization.py` ⚪ — รับ: `prioritize(scored, top_n=…)` → ทำ: เรียง `(confidence DESC, visit_frequency DESC)` ตัด top N → ส่ง: `list[ScoredPlace]` (N มาจากระยะของโรค ไม่ใช่ค่าคงที่)
- `trip_confidence.py` 🔵 — **(เพิ่ม 26 ส.ค. ไม่มีในเอกสารฉบับก่อน)** รับ: ปลายทางที่ผู้ป่วยขอไป + `known_places` + เขตอันตราย → ทำ: นิยาม "ความคุ้นเคย" ใหม่สำหรับที่ที่**ไม่เคยไป** = *อยู่ใกล้ที่ที่รู้จักแค่ไหน* (ใช้ `find_nearest_cluster` ซึ่งเคารพ `radius_m` ของแต่ละหมุด) + ปัจจัยเขตอันตราย → ส่ง: `{confidence, factors, nearest_place_name}`
  **ทำไมต้องแยกจาก `score_place`:** C-3 มีอยู่เพราะผู้ป่วยขอไปที่ที่ไม่เคยไป แต่ `score_place` คิด `frequency`/`familiarity` เทียบกับที่ที่เคยไป มีแต่ `proximity` (น้ำหนัก 0.35) ที่ไม่เป็นศูนย์ **เพดานจึงตันที่ 0.350 ตลอดกาล** = โชว์ตัวเลขที่วัดระยะทางแล้วเรียกมันว่าความปลอดภัย ซึ่งสอนให้ผู้ดูแลเลิกสนใจภายในสัปดาห์เดียว
- `__init__.py` ⚪ — export `UserContext, build_user_context, ScoredPlace, score_place, generate_recommendations, prioritize` (จงใจ **ไม่** import `ranker` เพื่อไม่ลาก sklearn เข้ามาทุกครั้ง)
- `recommendation.py` (API) ⚪ — รับ: `GET /api/recommendation/{patient_id}?lat&lng` → ทำ: `get_behavioral_profile` (+`get_latest_gps`) → `build_user_context` → `load_ranker(patient_id)` → `get_user` อ่าน `severity_level` เลือก `top_n` → `prioritize(...)` → ส่ง: `RecommendationResponse` (ไม่มี profile → `status="no_profile"` พร้อมลิสต์ว่าง ไม่ใช่ 404)

**ระดับไฟล์ (โมเดล + offline)**
- `ranker.py` 🟢 — รับ: `Module5Ranker(kind="histgbt"|"logistic")` / `.fit(train_events, frozen_places, label_fn)` / `.score_places(...)` / `load_ranker(patient_id)` → ทำ: pointwise learning-to-rank — **`HistGradientBoostingClassifier`** (ตัวจริง: max_depth=3, leaf=8, lr=0.1, l2=1.0 ; เลือก `max_iter` จาก (25,50,100,200) ด้วย temporal tail 9 วัน) + **`LogisticRegression`** (ตัวเทียบ) ; จัดอันดับด้วย `predict_proba[:,1]` ; weather ที่ไม่รู้เฉลี่ยข้ามทุก bucket ; เก็บ pickle `ranker_patient_{id}.pkl` → ส่ง: `dict[cluster_id→score]` (`score_places`) หรือ list เรียงแล้ว (`rank_event`)
- `featurize.py` ⚪ — รับ: `pair_row/rows_for_event/PlaceStatsNorm.from_places` (เรียกโดย `ranker`) → ทำ: สร้าง 8 features คงที่ `(slot_morning, is_weekend, w_sunny, w_rainy, w_hot, distance_km, frequency, familiarity)` ; `familiarity = log1p(stay)/log1p(max)` กันโมเดลเดาแต่ "บ้าน" → ส่ง: `list[float]` (+ label ตอนฝึก)
- `app/mock/data_source.py` 🔵 — **(ย้ายมาจาก `module5_recommend/`)** รับ: `MockDataSource(n_days=90, seed=42, ...)` → ทำ: **สร้างข้อมูลจำลอง** (seeded `RandomState`) — 5 สถานที่ (home/market/park/temple/clinic ห่าง >400m ให้ DBSCAN แยกออก), context `(slot, weekend, weather)→การกระจายสถานที่` (winner ≤0.65, noise 17.5%) ; จงใจไม่ปล่อย visit_frequency/avg_stay_time (กัน leakage) → ส่ง: `decision_events()`, `raw_gps()`, `effective_distribution()`
- `app/mock/weather_provider.py` 🔵 — **(ย้ายมาจาก `module5_recommend/`)** รับ: `MockWeather(n_days, slots, seed=7)` → ทำ: **สร้างสภาพอากาศจำลอง** (seeded) ลงตาราง `(day,slot)→bucket` ; `BUCKETS=("sunny","rainy","hot")` → ส่ง: `bucket(day_index, slot)` (และ `BUCKETS` ให้ featurize/ranker)
- `evaluation.py` 🔵 — รับ: `final_honesty_report(source, cut_day=63)` / `print_final_report()` → ทำ: กรรมการวัดความซื่อสัตย์ — split เวลา 70/30 (ไม่สลับ), freeze สถิติสถานที่จากคลัสเตอร์ **หน้าต่างฝึกเท่านั้น** (Module 1, กัน leakage), baselines (majority, context-blind), oracle เชิงวิเคราะห์, top-1/recall@2, **paired bootstrap CI** (N=1000, seed=2024), weather ablation + permutation importance, expanding-window CV, cold-start 30 วัน ; ผ่านเมื่อ full model ชนะทั้ง majority และ context-blind ที่ CI lower bound > 0 → ส่ง: dict รายงาน + คำตัดสิน go/no-go (offline เท่านั้น, API ไม่เรียก)

**🔗 ลำดับเรียก runtime**
router → `build_user_context` → `load_ranker` → `generate_recommendations(ctx, ranker)` (→ `ranker.score_places` → `featurize.pair_row` และ `score_place`) → `prioritize`

---

## โครงสร้าง `known_places` (จุดเชื่อมกลางของทุกโมดูล)

**แก้ 27 ส.ค.: มีผู้เขียนสองคน ไม่ใช่ Module 1 คนเดียว** และในทางปฏิบัติ**ผู้เขียนที่ทำงานจริงคือผู้ดูแล**
เขียนโดย `api/places.py` (หมุดที่คนปัก) และ `behavior_pipeline` (ผลคลัสเตอร์ — ไม่มีใครเรียก) →
อ่านโดย **Module 2, 3, 4, 5** ผ่าน `crud.get_behavioral_profile`

แต่ละ element ใน JSON array:
```
{ "cluster_id": int,        # แจกใหม่ 0,1,2… ตามลำดับทุกครั้งที่เขียน — อย่าเก็บถาวร
  "place_name": str,        # ชื่อที่ผู้ดูแลตั้ง — ไม่มีในรายการที่มาจากการเรียนรู้
  "latitude": float, "longitude": float,
  "radius_m": float,        # รัศมีของหมุดนี้เอง (ไม่มี = 150 m) M2/M4 เทียบกับค่านี้
  "visit_frequency": int,   # M5 ใช้คิด frequency, M3 ใช้เลือก dest, M4 ใช้จัดอันดับ target
  "avg_stay_time": float,   # วินาที — M5 ใช้คิด familiarity
  "source": "manual"|"learned",
  "is_home": bool }         # มีได้หมุดเดียวที่เป็น true
```

⚠️ **สองผู้เขียนใช้หน่วยคนละแบบ และนี่เคยทำให้หมุดถูก "กลบ" ไม่ใช่ถูกลบ** — หมุดใช้สเกล
rank (100/40/10/3) และวินาที ส่วนคลัสเตอร์ปล่อยจำนวนจุด GPS ดิบ (วัดได้ถึง 2,978) และนาที
ผู้บริโภคทุกตัว normalize แบบสัมพัทธ์ (`get_familiarity` หารด้วย `max(visit_frequency)`)
ผสมกันดิบ ๆ แล้วหมุดบ้านจะร่วงจาก familiarity 1.000 เหลือ 0.034 **ทั้งที่ผู้ป่วยนั่งอยู่ในบ้านตัวเอง**
และหน้าจอแอดมินยังโชว์หมุดครบทุกอัน กฎการผสม + ปรับสเกลอยู่ที่ `ai/module1_behavior/known_places.py`
ซึ่ง**ผู้เขียนทั้งสองฝั่ง import ตัวเดียวกัน** สเกลจึงเลื่อนออกจากกันไม่ได้

## ฟังก์ชัน crud ที่เป็นข้อต่อระหว่างโมดูล
| ฟังก์ชัน | เขียน/อ่าน | ใครใช้ |
|---|---|---|
| `save_gps_point()` | เขียน `GPSData` | gps_processor (M1) |
| `get_gps_history(db, patient_id, days)` | อ่านประวัติ GPS | M1 (behavior_pipeline), 2, 3, 4 |
| `get_latest_gps(db, patient_id)` | อ่าน GPS ล่าสุด | M3, 4, 5 |
| `upsert_behavioral_profile()` | เขียน `BehavioralProfile.known_places` | M1 |
| `get_behavioral_profile(db, patient_id)` | อ่าน profile | M2, 3, 4, 5 |
| `save_risk_score()` | เขียน `RiskScore` | M3 (risk.py) |
| `save_alert()` | เขียน `Alert` | M3 (risk.py), M4 (search_area.py) |
| `get_alerts()` / `get_alert()` / `set_alert_resolved()` | อ่าน/ปิด `Alert` | `api/alerts.py` (22 ส.ค.) |
| `get_recent_track()` | อ่านเส้นทางล่าสุด | `api/tracking.py` (22 ส.ค.) |
| `upsert_device_token()` / `get_caregiver_tokens()` / `delete_device_token()` | FCM token ผู้ดูแล | `api/devices.py`, `services/notification.py` |
| `seconds_since_last_push()` / `record_push()` | state ของ push cooldown | `services/notification.py` |
| `get_caregiver_ids()` | **ตรวจสิทธิ์เข้าถึงคนไข้ — เช็คว่าผู้เรียกอยู่ใน set ของผู้ดูแล** (28 ส.ค. เดิมเทียบเท่ากับผู้ดูแลคนเดียว) | `services/auth.py` · `api/pairing.py` · `api/trip_requests.py` |
| `get_caregiver_id()` | คืน**ผู้ดูแลหลักคนเดียว** สำหรับที่ที่ต้องการคำตอบเดียว — docstring บอกไว้ว่าเป็นคำถามที่ผิดสำหรับการตรวจสิทธิ์ | ผู้เรียกที่ต้องการคนเดียวจริง ๆ |
| `link_caregiver()` | เขียน `patient_caregivers` | `crud.create_user` (ตอนสร้างผู้ป่วย) · `api/pairing.py` (redeem รหัสเชิญ) |
| `create_caregiver_invite()` / `get_caregiver_invite()` / `mark_caregiver_invite_used()` | รหัสเชิญผู้ดูแลคนถัดไป | `api/pairing.py` (28 ส.ค.) |
| `update_user_location()` | เขียนตำแหน่งล่าสุดของ**ผู้ดูแล** ลง `users` (ไม่มีตารางประวัติ) | `api/users.py` (`PUT /api/caregivers/{id}/location`, 28 ส.ค.) |
| `get_user()` | อ่านทั้งแถว `users` (ชื่อ, uid, `severity_level`) | `api/pairing.py`, `api/recommendation.py`, `api/search_area.py`, `api/trip_requests.py` |
| `create_pairing_code()` / `get_pairing_code()` / `mark_pairing_code_used()` | รหัสจับคู่เครื่อง | `api/pairing.py` (26 ส.ค.) |
| `create_trip_request()` / `get_trip_request(s)()` / `decide_trip_request()` | คำขออนุมัติเดินทาง C-3 | `api/trip_requests.py` (26 ส.ค.) |

## สรุปการเชื่อมข้ามโมดูล (ใครเรียกใคร)
- **M1 → ทุกโมดูล:** เขียน `known_places` เป็น input กลาง
- **M2 → M3:** M3 (`risk_data_collection`) import และ fit/เรียก `WanderingDetector`, `StopConfusionClassifier`, `RoutePredictor`, `cluster_matcher` โดยตรง
- **M3 → M4:** M4 (`search_area.py`) เรียก `detect_gps_gap()` ของ M3 เพื่อเช็ก GPS หาย ; `gps_failure_handling` คืน dict ที่เป็น handoff contract ของ M4
- **M1 → M5 (offline):** `evaluation.py` ใช้ `preprocess_gps` + `cluster_places` ของ M1 บนหน้าต่างฝึก
- **M2 → M4 (ยังไม่ต่อ):** `wandering_score` ที่ `adjust_radius` รองรับ ยังถูกส่งเป็น `None`
- **`users.severity_level` → M4 และ M5:** ระยะของโรคเปลี่ยนทั้งรัศมีค้นหาและจำนวนรายการแนะนำ
  เป็นเส้นข้อมูลที่ไม่ผ่าน `behavioral_profiles` เส้นเดียวในระบบ
- **M5 → C-3:** `trip_confidence` ใช้ `find_nearest_cluster` ของ M2 กับ `known_places` ของหมุด
  แต่**ไม่**ใช้ `score_place` ด้วยเหตุผลเรื่องเพดาน 0.350 ข้างบน

---

## เส้นทางที่เพิ่มเข้ามาหลัง 19 ส.ค. (นอกโมดูล AI)

ไม่ใช่ AI แต่เป็นเส้นที่ทำให้ผลของโมดูล AI ออกไปถึงคนจริง

- `api/gps.py` ⚪ — หลังบันทึก GPS สำเร็จ เรียกฟังก์ชัน scoring ของ `api/risk.py`
  เอง **throttle 1 ครั้ง/60 วิ/คนไข้** อ่านเวลาจาก `risk_scores.calculated_at`
  (ไม่ใช่ state ในหน่วยความจำ — รอดรีสตาร์ต) · ห่อ try/except: risk พังห้ามทำให้
  การบันทึก GPS ล้มเหลว
- `services/notification.py` ⚪ — รับ: `Alert` ที่เพิ่งเขียน → ทำ: หา caregiver จาก
  `patient_caregivers` (**ผู้ดูแลทุกคน** ตั้งแต่ 2026-08-28 เดิมคือ `users.caregiver_id` คนเดียว) → เช็ค cooldown จากตาราง `push_notifications`
  (`push_cooldown_seconds` = 600 ใน KB, แยกตาม `(คนไข้, alert_type)`) → ส่ง FCM
  → บันทึกว่าส่งแล้ว · **ไม่ raise ทุกกรณี** เพราะอยู่ปลายทางของ GPS ที่บันทึกไปแล้ว
- `api/tracking.py` / `api/alerts.py` ⚪ — ฝั่งอ่านสำหรับแอปผู้ดูแล
  (`GET /api/patients/{id}/track`, `GET /api/patients/{id}/alerts`,
  `PATCH /api/alerts/{id}`) ย้ายมาจาก `/demo/*` ใน `demo_server.py` เมื่อ 22 ส.ค.
- `api/devices.py` ⚪ — `POST /api/devices/token` เก็บ FCM token ของเครื่องผู้ดูแล
- `services/auth.py` ⚪ — ตรวจ Firebase ID token → `users.id` → ตรวจว่าเป็นคนไข้เอง
  หรือผู้ดูแลของคนไข้คนนั้น · คุมด้วย env `AUTH_ENABLED` (default ปิด)
- `ai/module1_behavior/known_places.py` ⚪ — กฎการผสม `known_places` จากสองผู้เขียน
  (หมุดของผู้ดูแล กับผลคลัสเตอร์ของ Module 1) merge + ปรับสเกลให้อยู่บนแกนเดียวกัน
  ก่อนเขียนลง DB

---

## เส้นทางที่เพิ่มเข้ามาเมื่อ 26–27 ส.ค. (ไม่มีในเอกสารฉบับก่อนเลย)

ทั้งหมดนี้ไม่ใช่โมดูล AI แต่เป็นเส้นที่ตัดสินว่าใครได้เห็นอะไร และผู้ป่วยจะเข้าระบบได้ไหม

### จับคู่เครื่องผู้ป่วย — `api/pairing.py`

**ทำไมต้องมี:** ทุกอย่างในระบบยึดตัวตนจาก Firebase แต่ผู้ป่วยอัลไซเมอร์ถือ email + password
ไม่ได้ ทิศทางจึงถูกกลับด้าน — ผู้ดูแลสร้างผู้ป่วย **เซิร์ฟเวอร์เลือก Firebase uid ให้ก่อนที่
เครื่องผู้ป่วยจะเคยถูกแตะ** แล้วคืนรหัสสั้นมา

```
POST /api/patients  → users (uid สุ่ม, severity_level) + pairing_codes (8 ตัว, 24 ชม.)
POST /api/pair      → ตรวจรหัส → firebase_admin.auth.create_custom_token(uid)
                    → ตั้ง used_at → คืน {patient_id, firebase_custom_token, severity_level}
GET  /api/patients/{id} → {patient_id, name, severity_level}   (27 ส.ค.)
```

`services/auth.py` **ไม่ได้เพิ่มทางเดินที่สองเลย** — หลัง `signInWithCustomToken` เครื่องผู้ป่วย
เป็น bearer-token client ธรรมดา นี่คือเหตุผลเดียวที่ `AUTH_ENABLED` เปิดได้ ถ้าไม่มีเส้นนี้
การเปิด flag จะ 403 ทุก `POST /api/gps` ซึ่งไม่ใช่ปัญหาการล็อกอิน แต่คือเส้นข้อมูลทั้งเส้นดับ

**`GET /api/patients/{id}` มีเพราะ `/api/pair` ตอบได้ครั้งเดียว** — รหัสใช้ครั้งเดียวและแอปเรียก
pair ครั้งเดียวตลอดชีวิตเครื่อง ลงแอปใหม่หรือผู้ดูแลแก้ระยะโรคทีหลังจึงต้องมีทางอ่านซ้ำ

### ปุ่ม SOS — `api/sos.py`

`POST /api/sos` → **ข้ามการคิดคะแนนทั้งหมด** → `save_alert(alert_type="sos", severity="critical")`
→ `notify_alert()` ด้วย cooldown ของตัวเอง (`sos_cooldown_seconds` = 60 วิ)

ทำไมไม่ใช้ `POST /api/gps` + flag: การคิดคะแนนถูก throttle 1 ครั้ง/60 วิ กดตอนวินาทีที่ 20
หลังรอบล่าสุด = ไม่มีการคำนวณ = ไม่มี alert = **ปุ่มเงียบ** พิกัดส่งมาหรือไม่ก็ได้
response มี field `push` เพราะ **`201` แปลว่า "บันทึกแล้ว" ไม่ใช่ "ถึงมือแล้ว"**

### ขออนุมัติเดินทาง C-3 — `api/trip_requests.py`

```
POST  /api/trip-requests            → trip_confidence.score(...) → trip_requests (แช่ confidence ไว้)
GET   /api/patients/{id}/trip-requests
PATCH /api/trip-requests/{id}       → status/decided_by/decided_at
                                    → ถ้าปฏิเสธ: save_alert(alert_type="trip_denied") ไม่ใช่ "sos"
```

Level 1 ได้ `status: "not_required"` และ**ไม่มีแถวถูกสร้าง** ; การปฏิเสธข้าม push เมื่อผู้ตัดสิน
เป็นผู้ดูแลคนเดียวที่มี (ไม่ต้องเตือนคนที่เพิ่งกดเอง)

### หมุดสถานที่ — `api/places.py`

```
POST /api/patients/{id}/places        → เขียนทับชุด manual ทั้งชุด (rank ไม่ใช่ตัวเลข)
PUT  /api/patients/{id}/places/home   → upsert เฉพาะบ้าน **ลบหมุดอื่นไม่ได้**
GET  /api/patients/{id}/places
```

`PUT .../places/home` มีเพราะหน้าจอเพิ่มผู้ป่วยของแอปเก็บ "สถานที่ปลอดภัย" แค่จุดเดียว
ถ้าให้มันยิง `POST` ทั้งชุด การส่ง "บ้านอย่างเดียว" ซ้ำหลังปักครบแล้วจะ**ลบหมุดที่เหลือทิ้งเงียบ ๆ
พร้อมสถานะ `201`** — แก้ที่โค้ด ไม่ใช่แก้ด้วยการขอให้คนอีกฝั่งจำ

⚠️ **การเขียนหมุดทุกครั้งล้าง `routine_patterns` ทิ้ง** เพราะกิจวัตรอ้างสถานที่ด้วย `cluster_id`
ซึ่งถูกแจกใหม่ตามลำดับ ถ้าไม่ล้าง "9 โมงมักอยู่วัด" จะกลายเป็น "9 โมงมักอยู่บ้าน" โดยเนื้อหาไม่เปลี่ยน

### 🛑 ปักแค่บ้านจุดเดียวไม่พอ — วัดมาแล้ว 26 ส.ค.

| ผู้ป่วยอยู่ที่ไหน | ปักแค่บ้าน | ปักครบ 4 ที่ |
|---|---|---|
| ที่บ้าน | 9.0 low | 9.0 low |
| ที่วัดที่ไปทุกวัน | **56.0 medium** | 15.0 low |
| ที่ตลาดที่ไปทุกวัน | **56.0 medium** | 15.0 low |
| 2.5 กม. หลงจริง | 56.0 medium | 56.0 medium |

หมุดเดียว → `familiarity` เป็น 0.000 ทุกที่ยกเว้นบ้าน **ระบบจึงแยก "อยู่ที่วัดที่ไปทุกวัน" กับ
"หลง 2.5 กม." ไม่ออก ทั้งคู่ได้ 56.0 เท่ากัน** และกฎ push แบบสะสม (≥50 ติดกัน 5 รอบ ที่ throttle
60 วิ) แปลว่าครอบครัวโดนเตือน **ทุกครั้งที่ผู้ป่วยไปวัด ประมาณ 5 นาทีหลังถึง**
**หมุดที่ขาดไม่ใช่ความแม่นยำที่หายไปนิดหน่อย มันคือการเตือนผิดทุกครั้งที่ออกจากบ้าน**
