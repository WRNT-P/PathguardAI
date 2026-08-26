# PathGuard AI — การไหลของข้อมูล (Data Flow) ครบทั้ง 5 โมดูล

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

**สรุปไฟล์ที่เป็น "โมเดลที่ฝึกจริง" (🟢) มี 6 ไฟล์:** `lstm_utils.py`, `place_clustering.py`, `sequence_learning.py`, `destination_prediction.py`, `wandering_detection.py`, `ranker.py` — คิดเป็นโมเดลที่ต่างกัน 4 ตัว: LSTM (ลำดับสถานที่), DBSCAN (จับกลุ่ม), IsolationForest (จับความผิดปกติ), Gradient-Boosted ranker (จัดอันดับคำแนะนำ)

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
- `place_clustering.py` 🟢 — รับ: DataFrame สะอาด → ทำ: **DBSCAN** (eps≈50m, min_samples=5, haversine) + คำนวณเวลาพำนักเฉลี่ยต่อ visit → ส่ง: `list[dict]` `{cluster_id, latitude, longitude, visit_frequency, avg_stay_time}`
- `sequence_learning.py` 🟢 — รับ: sequences/labels → ทำ: ห่อ `SequencePredictor` (LSTM) เพื่อ build/train/predict สถานที่ถัดไป → ส่ง: cluster_id ที่ทำนาย (ยังไม่ถูก `behavior_pipeline` เรียกใช้)
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
- `risk_score_calculation.py` 🔵 — รับ: dict ปกติ 5 ค่า → ทำ: ถ่วงน้ำหนัก `0.30*D + 0.25*W + 0.20*C + 0.15*Z + 0.10*F` → ส่ง: `{risk_score (0–100), risk_level (low/medium/high), contributions}`
- `gps_failure_handling.py` 🟡 — รับ: `(last_reading, now, threshold_s=600.0)` → ทำ: คิดช่องว่างเวลาเทียบเกณฑ์ 600 วิ (unknown ⇒ ถือว่า GPS หาย = ปลอดภัยไว้ก่อน) → ส่ง: `{gps_lost, gap_seconds, last_known}` — **dict นี้คือ handoff contract ที่ Module 4 บริโภค (ตั้งใจไม่เรียก Module 4 ตรง ๆ)**
- `emergency_decision_engine.py` 🟡 — รับ: `(risk_score, danger_zone)` → ทำ: กฎ `danger_zone OR risk_score > 80` (danger_zone มาก่อน) → ส่ง: `{emergency, reason, severity, alert_type}` (ไม่แตะ DB)
- `__init__.py` ⚪ — export `collect_risk_factors`, normalizers, `calculate_risk`, `detect_gps_gap`, `decide_emergency`
- `risk.py` (API — ไฟล์เดียวของ Module 3 ที่แตะ DB) ⚪ — รับ: `GET /api/risk/{patient_id}?lat&lng` → orchestrate ทั้ง pipeline → ส่ง: `RiskResponse` + เขียน `RiskScore`/`Alert`

**🔗 เส้นทางเต็มของ `GET /api/risk/{patient_id}`**
1. `crud.get_behavioral_profile` → profile ; `crud.get_gps_history(days=30)` → `gps_30d` ; `recent_gps = gps_30d[-30:]` ; `crud.get_latest_gps` → `latest`
2. `collect_risk_factors(...)` → fit + detect (W, D, C, familiarity, Z)
3. normalize → `calculate_risk(normalized)` → `{risk_score, risk_level, contributions}`
4. `detect_gps_gap(latest, now)` → `gps_available`
5. `save_risk_score(...)` เขียน `RiskScore`
6. `decide_emergency(...)` → ถ้า emergency → `save_alert(geofence/emergency)` ; ถ้า gps_lost → `save_alert(gps_loss, high)`
7. คืน `RiskResponse`

---

## Module 4 — Search Area (ประเมินพื้นที่ค้นหาจากตำแหน่งสุดท้าย)

**ระดับโมดูล**
- **เข้า:** `patient_id` + (last_lat/last_lng/last_speed/direction/time_missing — override ได้ผ่าน query) จาก `api/search_area.py` ; `known_places` (Module 1) + GPS ล่าสุด + ประวัติ 30 วัน (จาก DB) ; **เรียก `detect_gps_gap()` ของ Module 3** เพื่อยืนยันว่าผู้ป่วยหายจริง ; `wandering_score` ของ Module 2 ยังส่งเป็น `None` (ยังไม่ต่อสาย)
- **ออก:** `SearchAreaResponse` ให้ frontend ; เขียน `Alert (gps_lost, high)` เฉพาะเมื่อ `gps_lost=True`
- **หมายเหตุสถาปัตยกรรม:** 4 ไฟล์ AI **ไม่ import กันเอง** — ตัว orchestrator จริงคือ router `api/search_area.py :: get_search_area()` ที่ร้อยเรียงลำดับให้ ; ภายในแพ็กเกจมีแค่ `probability_area_estimation` เรียก `identify_target_locations` ของตัวเอง

**ระดับไฟล์**
- `last_known_position.py` 🔵 — รับ: `calculate_search_radius(last_speed_ms, time_missing_minutes)` + `extract_last_known(last_gps_record)` → ทำ: รัศมี = `speed * minutes * 60` เมตร (Distance = Speed × Time), speed default `1.4 m/s` เมื่อว่าง ; `extract_last_known` เลือก `smooth_*` (Kalman) ก่อน raw → ส่ง: `radius (float)` และ dict `{lat, lng, speed_ms, direction_deg, recorded_at, _meta}`
- `search_radius_adjustment.py` 🟡 — รับ: `adjust_radius(base_radius_m, known_places, origin_lat, origin_lng, wandering_score=None)` → ทำ: ถ้าไม่มีที่คุ้นเคยในรัศมี ×**1.50** ; `wandering_score ≥ 0.75` ×**1.30** ; `≤ 0.30` ×**0.80** (คูณทบกัน) → ส่ง: `{adjusted_radius_m, adjustment_reason, _meta}` (ป้อนต่อทั้ง simulator และ KDE)
- `movement_path_simulation.py` 🔵 — รับ: `PathSimulator(n_simulations=10_000).fit(gps_30d).simulate_paths(last_lat, last_lng, direction, radius, known_places)` → ทำ: **Monte Carlo** — 70% เอียงตาม bearing ในอดีต ±45°, 30% สุ่มทั่ว, ระยะ `r = radius·√U` (กระจายเต็มพื้นที่วง) + **A\* อย่างง่าย** ลากเส้น great-circle 10 จุดไปยัง top-5 ที่คุ้นในรัศมี → ส่ง: `{status, endpoints:[[lat,lng]], familiar_paths:[...], _meta}`
- `probability_area_estimation.py` 🔵 — รับ: `estimate_probability_zones(endpoints, known_places, radius, origin_lat, origin_lng)` (+ public `identify_target_locations`) → ทำ: กริด 50×50 (2500 เซลล์) + **KDE** (`scipy.stats.gaussian_kde`) บน endpoints → normalize 0–1 → แบ่งโซนตาม percentile **High ≥ p70, Medium p40–p70, Low < p40** (ถ้า scipy ไม่มี/เสื่อม fallback แบ่งตามระยะเป็น 3 ชั้น) → ส่ง: `{high_zone/medium_zone/low_zone:[{lat,lng,probability}], target_locations, grid_bounds, _meta}`
- `__init__.py` ⚪ — export `calculate_search_radius, extract_last_known, PathSimulator, estimate_probability_zones, identify_target_locations, adjust_radius`
- `search_area.py` (API) ⚪ — รับ: `GET /api/search-area/{patient_id}?...` → ทำ: อ่าน DB → `extract_last_known` → `detect_gps_gap` (M3) → `calculate_search_radius` → `adjust_radius` → `PathSimulator.fit().simulate_paths()` → `estimate_probability_zones` → ส่ง: `SearchAreaResponse` (+ alert ถ้าหาย)

**🔗 ลำดับเรียกใน `get_search_area()`**
`extract_last_known` → `detect_gps_gap` (Module 3) → `calculate_search_radius` → `adjust_radius` → `PathSimulator.fit → simulate_paths` → `estimate_probability_zones (→ identify_target_locations)`

---

## Module 5 — Recommend (แนะนำสถานที่น่าจะไป top 3) — runtime อ่านอย่างเดียว ไม่เขียน DB

โมดูลนี้มี **2 ชั้น**: (ก) **Runtime/API** ใช้กฎถ่วงน้ำหนัก และถ้ามีโมเดลที่ฝึกไว้ก็เสียบ learned ranker แทน ; (ข) **Offline train/eval** สร้างข้อมูลจำลอง ฝึก ranker แล้ว pickle เก็บไว้ให้ runtime โหลด

**ระดับโมดูล**
- **เข้า (runtime):** `patient_id` + lat/lng (จาก `api/recommendation.py`) ; `known_places` (Module 1) + GPS ล่าสุด ; โมเดลต่อผู้ป่วยจากไฟล์ `ranker_patient_{id}.pkl` (ถ้ามี)
- **ออก (runtime):** `RecommendationResponse` (top 3 พร้อม confidence) — **ไม่เขียน DB**
- **เข้า (offline):** **ข้อมูลจำลอง** จาก `MockDataSource`/`MockWeather` (ไม่ใช่ข้อมูลจริง) + คลัสเตอร์จาก Module 1 บนหน้าต่างฝึก
- **ออก (offline):** artifact `ranker_patient_{id}.pkl` + รายงาน go/no-go

**ระดับไฟล์ (runtime path)**
- `user_context_analysis.py` ⚪ — รับ: `build_user_context(profile, lat, lng, now)` (profile = ORM ของ Module 1 หรือ None) → ทำ: `_parse_known_places()` แปลง `.known_places` JSON เป็น `list[dict]` (ทน error, คืน `[]`), ตั้ง flag `has_profile/has_location` → ส่ง: dataclass `UserContext`
- `recommendation_generation.py` 🔵 — รับ: `generate_recommendations(ctx, ranker=None)` / `score_place(place, ctx, ml_score=None)` → ทำ: ผสมกฎโปร่งใส `WEIGHTS {frequency:0.45, proximity:0.35, familiarity:0.20}` (proximity = `1/(1+haversine)` เมื่อมีตำแหน่ง) ; **ถ้าส่ง `ranker` มา จะใช้ `ranker.score_places(...)` เป็น confidence แล้วติดธง `scorer="ml"`** (ไม่มีก็ `scorer="rules"` ไม่แอบอ้างว่าเป็น ML) → ส่ง: `list[ScoredPlace]`
- `recommendation_prioritization.py` ⚪ — รับ: `prioritize(scored, top_n=3)` → ทำ: เรียง `(confidence DESC, visit_frequency DESC)` ตัด top N → ส่ง: `list[ScoredPlace]` 3 อันดับแรก
- `__init__.py` ⚪ — export `UserContext, build_user_context, ScoredPlace, score_place, generate_recommendations, prioritize` (จงใจ **ไม่** import `ranker` เพื่อไม่ลาก sklearn เข้ามาทุกครั้ง)
- `recommendation.py` (API) ⚪ — รับ: `GET /api/recommendation/{patient_id}?lat&lng` → ทำ: `get_behavioral_profile` (+`get_latest_gps`) → `build_user_context` → `load_ranker(patient_id)` → `prioritize(generate_recommendations(ctx, ranker), top_n=3)` → ส่ง: `RecommendationResponse` (ไม่มี profile → `status="no_profile"`)

**ระดับไฟล์ (โมเดล + offline)**
- `ranker.py` 🟢 — รับ: `Module5Ranker(kind="histgbt"|"logistic")` / `.fit(train_events, frozen_places, label_fn)` / `.score_places(...)` / `load_ranker(patient_id)` → ทำ: pointwise learning-to-rank — **`HistGradientBoostingClassifier`** (ตัวจริง: max_depth=3, leaf=8, lr=0.1, l2=1.0 ; เลือก `max_iter` จาก (25,50,100,200) ด้วย temporal tail 9 วัน) + **`LogisticRegression`** (ตัวเทียบ) ; จัดอันดับด้วย `predict_proba[:,1]` ; weather ที่ไม่รู้เฉลี่ยข้ามทุก bucket ; เก็บ pickle `ranker_patient_{id}.pkl` → ส่ง: `dict[cluster_id→score]` (`score_places`) หรือ list เรียงแล้ว (`rank_event`)
- `featurize.py` ⚪ — รับ: `pair_row/rows_for_event/PlaceStatsNorm.from_places` (เรียกโดย `ranker`) → ทำ: สร้าง 8 features คงที่ `(slot_morning, is_weekend, w_sunny, w_rainy, w_hot, distance_km, frequency, familiarity)` ; `familiarity = log1p(stay)/log1p(max)` กันโมเดลเดาแต่ "บ้าน" → ส่ง: `list[float]` (+ label ตอนฝึก)
- `data_source.py` 🔵 — รับ: `MockDataSource(n_days=90, seed=42, ...)` → ทำ: **สร้างข้อมูลจำลอง** (seeded `RandomState`) — 5 สถานที่ (home/market/park/temple/clinic ห่าง >400m ให้ DBSCAN แยกออก), context `(slot, weekend, weather)→การกระจายสถานที่` (winner ≤0.65, noise 17.5%) ; จงใจไม่ปล่อย visit_frequency/avg_stay_time (กัน leakage) → ส่ง: `decision_events()`, `raw_gps()`, `effective_distribution()`
- `weather_provider.py` 🔵 — รับ: `MockWeather(n_days, slots, seed=7)` → ทำ: **สร้างสภาพอากาศจำลอง** (seeded) ลงตาราง `(day,slot)→bucket` ; `BUCKETS=("sunny","rainy","hot")` → ส่ง: `bucket(day_index, slot)` (และ `BUCKETS` ให้ featurize/ranker)
- `evaluation.py` 🔵 — รับ: `final_honesty_report(source, cut_day=63)` / `print_final_report()` → ทำ: กรรมการวัดความซื่อสัตย์ — split เวลา 70/30 (ไม่สลับ), freeze สถิติสถานที่จากคลัสเตอร์ **หน้าต่างฝึกเท่านั้น** (Module 1, กัน leakage), baselines (majority, context-blind), oracle เชิงวิเคราะห์, top-1/recall@2, **paired bootstrap CI** (N=1000, seed=2024), weather ablation + permutation importance, expanding-window CV, cold-start 30 วัน ; ผ่านเมื่อ full model ชนะทั้ง majority และ context-blind ที่ CI lower bound > 0 → ส่ง: dict รายงาน + คำตัดสิน go/no-go (offline เท่านั้น, API ไม่เรียก)

**🔗 ลำดับเรียก runtime**
router → `build_user_context` → `load_ranker` → `generate_recommendations(ctx, ranker)` (→ `ranker.score_places` → `featurize.pair_row` และ `score_place`) → `prioritize`

---

## โครงสร้าง `known_places` (จุดเชื่อมกลางของทุกโมดูล)

เขียนโดย **Module 1** (`crud.upsert_behavioral_profile`) → อ่านโดย **Module 2, 3, 4, 5** (`crud.get_behavioral_profile`)
แต่ละ element ใน JSON array:
```
{ "cluster_id": int, "latitude": float, "longitude": float,
  "visit_frequency": int,   # M5 ใช้คิด frequency, M3 ใช้เลือก dest, M4 ใช้จัดอันดับ target
  "avg_stay_time": float }   # M5 ใช้คิด familiarity
```

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
| `get_caregiver_id()` | ตรวจสิทธิ์เข้าถึงคนไข้ | `services/auth.py` |

## สรุปการเชื่อมข้ามโมดูล (ใครเรียกใคร)
- **M1 → ทุกโมดูล:** เขียน `known_places` เป็น input กลาง
- **M2 → M3:** M3 (`risk_data_collection`) import และ fit/เรียก `WanderingDetector`, `StopConfusionClassifier`, `RoutePredictor`, `cluster_matcher` โดยตรง
- **M3 → M4:** M4 (`search_area.py`) เรียก `detect_gps_gap()` ของ M3 เพื่อเช็ก GPS หาย ; `gps_failure_handling` คืน dict ที่เป็น handoff contract ของ M4
- **M1 → M5 (offline):** `evaluation.py` ใช้ `preprocess_gps` + `cluster_places` ของ M1 บนหน้าต่างฝึก
- **M2 → M4 (ยังไม่ต่อ):** `wandering_score` ที่ `adjust_radius` รองรับ ยังถูกส่งเป็น `None`

---

## เส้นทางที่เพิ่มเข้ามาหลัง 19 ส.ค. (นอกโมดูล AI)

ไม่ใช่ AI แต่เป็นเส้นที่ทำให้ผลของโมดูล AI ออกไปถึงคนจริง

- `api/gps.py` ⚪ — หลังบันทึก GPS สำเร็จ เรียกฟังก์ชัน scoring ของ `api/risk.py`
  เอง **throttle 1 ครั้ง/60 วิ/คนไข้** อ่านเวลาจาก `risk_scores.calculated_at`
  (ไม่ใช่ state ในหน่วยความจำ — รอดรีสตาร์ต) · ห่อ try/except: risk พังห้ามทำให้
  การบันทึก GPS ล้มเหลว
- `services/notification.py` ⚪ — รับ: `Alert` ที่เพิ่งเขียน → ทำ: หา caregiver จาก
  `users.caregiver_id` → เช็ค cooldown จากตาราง `push_notifications`
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
