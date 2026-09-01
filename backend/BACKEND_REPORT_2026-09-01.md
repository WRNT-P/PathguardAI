# รายงานฝั่ง Backend — 1 กันยายน 2026

สรุปทุกอย่างที่ฝั่ง backend ทำ หลังจากคุณ push `a96a841` ขึ้น `frontend1`

เอกสารนี้เป็น **รายงานว่าเกิดอะไรขึ้น** ส่วน **สิ่งที่ต้องทำต่อพร้อมโค้ด** อยู่ที่
`backend/APP_SYNC_2026-09-01.md` — ถ้ามีเวลาอ่านแค่ไฟล์เดียว อ่านไฟล์นั้น

**สถานะ** `main = 9637a2d = origin/main` · 419 tests ผ่าน · 33 `/api/*` routes ·
12 commits · +1,475 / −35 บรรทัด

---

## 0. สรุปหกบรรทัด

1. **งานที่คุณส่งมาผ่านหมด** — ไล่เทียบทุก field กับ pydantic model ทีละตัว **ไม่ผิดสักตัวเดียว**
   จาก 3 endpoint เป็น 9
2. **ปัญหาที่ใหญ่ที่สุดสองข้อปิดแล้ว** — หมุดถูกส่งขึ้นจริง และ `patient_id` เก็บลงเครื่องแล้ว
3. **Module 2 ไม่ได้ตาย** — 3 ใน 4 ส่วนรันอยู่ทุกจุด GPS คิดเป็น **75% ของคะแนนความเสี่ยง**
4. **TensorFlow ลงบน Windows ได้** — ที่ลงไม่ได้คือ CUDA/GPU ไม่ใช่ตัว TensorFlow
5. **สร้างของใหม่ให้ 2 ตัว** — `GET /api/me` และ `GET /api/predict-destination/{id}`
   ตัวหลังคือ path ที่รายงานอ้างถึงและเคยตอบ 404 มาตลอด
6. **เจอบั๊ก 6 ข้อในโค้ดคุณ** ข้อแรกทำให้ Safe Zone Navigation ตายเงียบทั้งฟีเจอร์บน Android

---

## 1. ตรวจ git ก่อนเริ่ม

- `git fetch --all --prune` → ขยับแค่ `origin/frontend1` (`14be99a` → `a96a841`)
  ส่วน `origin/main` ไม่ขยับ
- `main` ของเรา = `origin/main` (ahead 0 / behind 0) → ไม่มีอะไรค้างไม่ได้ push
- `a96a841` แตะเฉพาะโฟลเดอร์ `flutter_app/` และทดสอบ merge แบบไม่จริงแล้วได้ **0 conflict**
  → เอาลง `main` ได้สะอาดเมื่อไหร่ก็ได้
- `.env` ไม่ได้ถูก commit (`flutter_app/.gitignore:48-49`) — repo เป็น public เลยเช็คให้

---

## 2. รีวิวโค้ดที่คุณส่งมา

### 2.1 สิ่งที่ผ่าน

ไล่เทียบ **ทุก field ที่แอปส่ง กับ pydantic model ในโค้ดจริง ทีละตัว** ไม่ได้ดูจากเอกสาร
ผลคือ **ไม่ผิดสักตัวเดียว** ทั้ง 9 endpoint

| แอปเรียก | เทียบกับ |
|---|---|
| `POST /api/register` | `UserCreate` → 201 คืน `id` |
| `POST /api/patients` | `PatientIn` → 201 คืน `patient_id` + `pairing_code` |
| `POST /api/patients/{id}/places` | `PlaceIn` — enum `daily_live`/`most_days`/`all_day`/`few_hours` ตรง Literal เป๊ะ |
| `POST /api/pair` | `PairIn` → 200 + `firebase_custom_token` + `severity_level` |
| `GET /api/patients/{id}` | `PatientProfileOut` |
| `POST /api/gps` | `GPSDataCreate` ครบทุก field |
| `POST /api/sos` | `SOSIn` → เช็ค 201 ถูก |
| `POST /api/trip-requests` | `TripRequestIn` → อ่าน `confidence` |
| `GET /api/recommendation/{id}` | `place_name` / `latitude` / `longitude` / `confidence_pct` |

รายละเอียดที่ทำถูกและพลาดง่ายมาก — กรอง `heading` ที่เป็น −1 ทิ้งก่อนส่งเป็น `direction`
ถ้าส่ง −1 ไปตรง ๆ จะได้ 422 ทันที

### 2.2 สองปัญหาใหญ่ที่สุดปิดแล้ว

- **หมุดสถานที่** เคยถูกเก็บมาแล้วโยนทิ้ง ตอนนี้ส่งขึ้นจริงที่
  `caregiver_homepage_screen.dart:99` → **Module 3 คิดคะแนนแบบเต็ม 5 ปัจจัยได้แล้ว**
  ไม่ใช่โหมด partial ที่แยก "อยู่บ้าน" กับ "หลง 2.5 กม." ไม่ออก
- **`patient_id`** เก็บลง `SharedPreferences` แล้ว ปิดแอปแล้วไม่หาย
- และ caregiver login ถูกเขียนใหม่ให้มี controller + Firebase Auth จริง

### 2.3 บั๊ก 6 ข้อที่เจอ

**ข้อ 1 — พิมพ์ผิดตัวอักษรเดียว ทำให้ Safe Zone Navigation ตายเงียบ 100% บน Android**

```dart
// safe_zone_service.dart:9
: dotenv.env['ANDROID_GOOGLE_MAPS_API_KEYS']!;   // ← มี S เกิน
```

อีกสองไฟล์ใช้ชื่อไม่มี S (`directions_service.dart:31`, `places_service.dart:16`)
→ คืน null → `!` → `Null check operator used on a null value` → throw

และมัน **ไม่ crash ให้เห็น** เพราะ `_handleSOS()` ครอบด้วย `try { ... } catch (_) {}`
→ `safePlace` เป็น null เสมอ → เด้งไป `SosContactsScreen` ทุกครั้ง เหมือน "แถวนี้ไม่มีที่ปลอดภัย"
**แย่กว่า crash เพราะไม่มีอะไรบอกว่าพัง**

แนะนำให้แยก `catch` ของ Geolocator กับของ Places ออกจากกันด้วย ไม่งั้นความล้มเหลว
คนละเรื่องจะดูเหมือนกันหมด

**ข้อ 2 — ยังไม่มี FCM เลย → W3 ผ่านไม่ได้**

ไม่มี `firebase_messaging` ใน `pubspec.yaml` และไม่มีใครเรียก `POST /api/devices/token`
→ alert ทุกชนิด (wandering / sos / gps_loss / trip_denied) จบที่เซิร์ฟเวอร์
`notify_alert` คืน `no_caregiver` เสมอ ไม่มีทางถึงมือถือผู้ดูแล

**ข้อ 3 — GPS ไม่ทำงานตอนหน้าจอดับ → W2 ผ่านไม่ได้**

`AndroidManifest.xml:2-3` มีแค่ `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION`
ไม่มี `ACCESS_BACKGROUND_LOCATION` ไม่มี foreground service
และ `stopGpsReporting()` อยู่ใน `dispose()` ของทั้งสองหน้า homepage → ออกจากหน้าคือหยุดส่ง

**ข้อ 4 — `distanceFilter: 10` ทำให้ "นั่งเฉย ๆ" กับ "แบตหมด" แยกไม่ออก**

ไม่ขยับ 10 เมตร = ไม่มี event = ไม่ส่งอะไรเลย และฝั่งเราให้คะแนนเฉพาะตอนมีจุดเข้ามา
(ไม่มี job รันเป็นรอบ) แปลว่าโทรศัพท์แบตหมด ปิดแอป ปิด location กับนั่งดูทีวีอยู่บ้าน
ให้ผลเหมือนกันเป๊ะ — เงียบทั้งคู่ ไม่มีอะไรในระบบรู้ความต่าง

แก้ด้วย heartbeat ส่งตำแหน่งทุก ~60 วิ แม้ไม่ขยับ (โค้ดร่างอยู่ใน `APP_SYNC` §5.4)

**ข้อ 5 — คำขอเดินทางค้าง `pending` ถาวร**

`trip_approval_service.dart` ยิง `POST /api/trip-requests` แล้วอ่านแค่ `confidence`
ทิ้ง `id` ที่เราคืนมา ส่วนอนุมัติ/ปฏิเสธไปทาง Firebase ล้วน ๆ
grep `apiPatch` ทั้งแอป — มีแต่นิยาม **ไม่มี call site เลย**

ผล: ทุกแถวใน `trip_requests` เป็น `pending` ตลอดกาล, alert `trip_denied` ไม่เคยเกิด,
C-3 Confidence Score ที่รายงานอ้างอิงไม่เคยถูกบันทึกผลการตัดสินใจ

Firebase เป็นตัวส่งสดต่อไปได้ แค่ขอให้ยิง `PATCH /api/trip-requests/{id}` ด้วยตอนกด

**ข้อ 6 — `patient_id` อาจเป็น null**

`trip_approval_service.dart:16` ส่ง `Session.instance.patientId` ตรง ๆ ไม่เช็ค null
(ต่างจาก `sos_service.dart:6` ที่เช็ค) → ถ้า session หายจะได้ 422

### 2.4 สี่ช่องว่างที่ยังไม่มีใครทำ

backend พร้อมหมดแล้ว ขาดแต่หน้าจอ — **ไม่บล็อก pilot แต่ควรรู้ไว้**

| ช่องว่าง | endpoint ที่รออยู่ |
|---|---|
| Module 4 Search Area ไม่มีหน้าจอเลย | `GET /api/search-area/{id}` — **ห้าม poll** |
| เขตอันตรายไม่มีหน้าจอสร้าง | `POST /api/danger-zones` |
| แจ้งเตือนไม่เคยถูกปิด | `PATCH /api/alerts/{id}` |
| ผู้ดูแลคนที่ 2 ไม่มี UI | `POST /api/patients/{id}/caregiver-invites` + `redeem-invite` |

⚠️ **ถ้าจะทำหน้าจอเขตอันตราย บอกเราก่อน** ทุก endpoint ของ `danger-zones` เช็คแค่
"ล็อกอินแล้ว" ไม่เช็คความเป็นเจ้าของ แปลว่าเครื่องคนไข้ก็ `DELETE` โซนได้
= ปิดเสียงเตือนฉุกเฉินเอง ตอนนี้ปลอดภัยเพราะยังไม่มีโซนสักอัน พอมีโซนจริงต้องปิดช่องนี้ก่อน

---

## 3. ห้าเรื่องที่เชื่อกันผิด

ทั้งห้าข้อตรวจจาก **โค้ดที่รันจริง** ไม่ใช่จากเอกสารหรือความจำ
และหนึ่งในนั้น **ฝั่งเราพูดผิดเอง**

### 3.1 "Module 2 — Not connected, and will never connect, by design" — ผิด

รายงานนิยาม Module 2 ไว้ 3 อย่าง **ตัดแค่อันเดียว**

| ส่วนของ Module 2 | สถานะจริง | ไฟล์ |
|---|---|---|
| LSTM destination | ❌ ตัดจริง | `lstm_utils.py:6` |
| Isolation Forest — wandering | ✅ **รันอยู่** | `wandering_detection.py:38,247` |
| Markov + Viterbi — route | ✅ **รันอยู่** | `route_prediction.py:144,305` |
| Stop/Confusion classifier | ✅ **รันอยู่** | `stop_confusion_classification.py` |

เส้นทางเรียกจริง: `POST /api/gps` → `gps.py:_score_risk_after_ingest` → `evaluate_risk`
→ `collect_risk_factors` ซึ่งอยู่ใน `risk_data_collection.py` และไฟล์นั้นเปิดหัวด้วย

```python
# app/ai/module3_risk/risk_data_collection.py:36-38
from app.ai.module2_prediction.wandering_detection import WanderingDetector
from app.ai.module2_prediction.stop_confusion_classification import StopConfusionClassifier
from app.ai.module2_prediction.route_prediction import RoutePredictor
# :171 .detect()   :184 .predict_route()   :209 .classify()
```

น้ำหนักจาก `seed_risk_rules.py:28-45` — route_deviation **0.30** + wandering **0.25**
+ confusion **0.20** = **75% ของคะแนน 0-100 มาจากโค้ด Module 2**
และตอนที่ยังไม่มีหมุด `wandering` เป็น 1 ใน 2 factor ที่ยังทำงานได้

**ทำไมเรื่องนี้สำคัญ** ถ้าตาราง "Module 2 ❌" เข้ารายงาน เราจะทิ้งเครดิต Isolation Forest,
Markov + Viterbi และ confusion classifier ที่ทำเสร็จแล้วไปฟรี ๆ ทั้งสามตัว

### 3.2 "Windows ลง TensorFlow ไม่ได้ เลยจะใช้ข้อมูลปลอม" — ผิด

```
$ pip install --dry-run tensorflow     # Windows 11 · Python 3.13.14 · AMD64
Would install ... keras-3.15.1 ... tensorflow-2.21.0
```

มี wheel ให้ ลงได้ สิ่งที่ Windows เสียไปตั้งแต่ TF 2.11 คือ **CUDA/GPU** ไม่ใช่ตัว TensorFlow
CPU-only ยังลงได้ปกติ และ LSTM ตัวนี้เล็กมาก CPU เหลือเฟือ
(เป็น dry-run เฉย ๆ ยังไม่ได้ลงจริง)

### 3.3 `README.md` ของเราโฆษณา endpoint ที่ไม่มีอยู่ — **นี่คือต้นตอ**

`README.md` เขียน `GET /api/predict-destination/{patient_id}` ไว้ในตาราง "Endpoints live today"
ทั้งที่ไม่ได้ mount มานานแล้ว **คุณไปตามหา endpoint นั้นเพราะตารางนี้** และ `README` คือ doc
ที่ teammates อ่าน (`CLAUDE.md` ถูก gitignore)

ตารางเดิมเขียนมือไว้ 15 แถว ทั้งที่มี route จริง 34 — ขาด `/api/sos`, `/api/pair`,
`POST /api/patients`, `gps/batch` และอีกหลายตัว **เขียนใหม่ให้ generate จาก `app.openapi()`
เพื่อไม่ให้เพี้ยนได้อีก**

### 3.4 `main.py` docstring ชวนให้ทำแอปพังทั้งตัว

เดิมเขียนว่า *"re-add the import and `include_router` line to bring it back"* ซึ่งอ่านแล้ว
เหมือนแก้บรรทัดเดียวและพังแค่ endpoint เดียว **ไม่ใช่ทั้งคู่**

```
app/api/prediction.py:6
  → destination_prediction.py:16
    → lstm_utils.py:6   import tensorflow as tf   ← ModuleNotFoundError
```

เป็น top-level import ทั้งสาย ถ้าไม่มี TF **แอปทั้งตัว boot ไม่ขึ้น**
`/api/gps` `/api/sos` `/api/pair` ตายไปด้วย เพราะ error เกิดตอน import `app.main`

แก้ docstring แล้ว และใส่คำเตือนใน `prediction.py` เองด้วย

### 3.5 ฝั่งเราพูดผิดเอง — แก้ใน `c580210`

เราบอกว่า *"แค่เปิด `RoutePredictor` ที่มีอยู่แล้วเป็น endpoint ก็ได้ตัวทำนายปลายทาง"*
**ผิด**

```python
def predict_route(self, recent_gps, destination_cluster_id, known_places) -> dict:
```

`destination_cluster_id` เป็น **input** มันทำนาย *เส้นทางไปยังปลายทางที่บอกมัน*
ไม่ใช่ทำนายว่าปลายทางคือที่ไหน และของจริงยิ่งกว่านั้น — **production ไม่ได้ทำนายปลายทางเลย**
ใช้ฮิวริสติกบรรทัดเดียวที่ `risk_data_collection.py:182`

```python
dest_id = max(known_places, key=lambda p: p.get("visit_frequency", 0))["cluster_id"]
# "ปลายทาง = ที่ที่ไปบ่อยที่สุด" — ในทางปฏิบัติคือบ้าน
```

แก้ใน `APP_SYNC_2026-09-01.md` §3 แล้ว โดย **ทิ้งกล่องบอกว่าฉบับแรกผิดยังไงไว้ให้เห็น
ไม่ได้ลบเงียบ ๆ** เพราะคุณอาจอ่านฉบับแรกไปแล้ว

---

## 4. ของใหม่ที่สร้างให้

| commit | อะไร |
|---|---|
| `224f02e` | **`GET /api/me`** (+7 tests) |
| `439024e` | `main.py` docstring |
| `221e95b` | `APP_SYNC_2026-09-01.md` |
| `b0665be` | `README` ตาราง endpoint generate จาก `app.openapi()` |
| `270fdbf` | **`GET /api/predict-destination/{id}`** (+12 tests) |
| `b947274` | `--uid` บน `scripts/create_caregiver.py` |
| `9637a2d` | อัปเดต `APP_SYNC` ว่า endpoint เสร็จแล้ว |

### 4.1 `GET /api/me` — สัญญา §14

**เพิ่มมาเพราะ `caregiver_login_screen.dart:110` ต้องอ่าน `CAREGIVER_TEST_ID` จาก `.env`
และนั่นไม่ใช่ความผิดฝั่งคุณ** ก่อนหน้านี้ backend **ไม่มี route ไหนตอบคำถามนี้ได้เลย**
`POST /api/register` เป็นตัวเดียวที่เคยคืน `users.id` และมันยิงครั้งเดียวตอนสมัคร
ผู้ดูแลที่ login ซ้ำหรือลงแอปเครื่องใหม่จึงหาทางกลับไปหา id ตัวเองไม่เจอ

ผลของการฮาร์ดโค้ด: **ผู้ดูแลทุกคนที่ login ซ้ำจะกลายเป็น account ทดสอบ**
คนไข้ที่สร้างไปอยู่ใต้แถวของคนอื่น และคนไข้ของตัวเองมองไม่เห็น

```json
{ "id": 21, "firebase_uid": "...", "name": "ผู้ดูแลทดสอบ",
  "role": "caregiver", "phone": "081-234-5678", "created_at": "..." }
```

`role` คืนมาด้วยเพราะ **ตอนนี้ไม่มีอะไรกันคนไข้ล็อกอินเข้าหน้าผู้ดูแลเลย** และมันจะดูเหมือน
สำเร็จด้วย เช็คได้ด้วยการเทียบครั้งเดียว

⚠️ **ต้องมี token เสมอ แม้ `AUTH_ENABLED` ปิดอยู่** เพราะคำถามของมันคือ "คุณคือใคร"
เรียกหลัง sign-in สำเร็จเท่านั้น — โค้ด Dart ร่างไว้ใน `APP_SYNC` §4

### 4.2 `GET /api/predict-destination/{id}` — สัญญา §15

**path เดิมที่เคยตอบ 404 ตอนนี้ตอบแล้ว และไม่ต้องลง TensorFlow**

`transition_matrix[current]` (`route_prediction.py:144`) คือการแจกแจงความน่าจะเป็น
ของสถานที่ถัดไปอยู่แล้ว เอา top-3 ออกมาก็ได้ Markov destination prediction ของจริง

**สามฟิลด์ที่เป็นหัวใจ ไม่ใช่ของประดับ** — ด้วยหมุดไม่กี่จุดและประวัติน้อย matrix จะเกือบแบน
แล้ว "33%" จะอ่านดูเหมือนคำตัดสินของโมเดล ทั้งที่คือ 1 หารด้วย 3
โปรเจกต์นี้เคยพลาดแบบนี้มาแล้วครั้งหนึ่ง (C-3 Confidence Score ที่ตันอยู่ 35% ตลอดกาล
เพราะวัดแค่ระยะทาง) และต้องเขียนโมดูลใหม่ทั้งตัว

- `scorer` — `"markov"` เสมอ ไม่เคยเป็น `"lstm"`
- `history_status` — `none` / `sparse` / `ok` · **ถ้าเป็น `none` ห้ามแสดงเปอร์เซ็นต์**
- `transitions_observed` — ตัวเลขดิบ ตรวจสอบได้ว่าทำไมผลออกมาแบบนั้น

และมันจงใจ **ไม่ตอบ** สองกรณี — ยืนนอกรัศมีหมุดทุกจุดคืน `unknown_current_place` ไม่เดา
เพราะ Markov ต้องมีจุดตั้งต้น (เคสหลงทางเป็นงานของ `/api/risk` ซึ่งไม่ต้องการจุดตั้งต้น)
และตัดแนวทแยงทิ้งก่อนจัดอันดับ ไม่งั้นจะทำนายว่า "อยู่บ้าน → จะไปบ้าน"

> 🟢 **มีให้แล้ว แต่ยังไม่ต้องต่อตอนนี้** ไม่มีหน้าจอไหนต้องใช้ ไม่มี W1–W4 ข้อไหนแตะมัน
> ไปลงบั๊ก 6 ข้อกับ FCM ก่อนคุ้มกว่ามาก ค่อยกลับมาต่อหลัง pilot ตอนมี GPS จริงแล้ว
> ตัวเลขจะมีความหมายขึ้นเยอะ

---

## 5. พิสูจน์กับของจริง

419 tests รันบน SQLite ในหน่วยความจำ ซึ่ง **ไม่พิสูจน์ว่าทำงานกับ Neon และ Firebase จริง**
เลยรันจริงทั้งสองฝั่ง

### 5.1 Markov ทำนายถูกตามข้อมูลที่ป้อนเข้าไป

สร้างคนไข้ทิ้ง 1 คน ป้อน **บ้าน→วัด 10 ครั้ง / บ้าน→ตลาด 3 ครั้ง** (26 จุดผ่าน
`POST /api/gps/batch`)

```json
{ "status": "ok", "scorer": "markov",
  "history_status": "ok", "transitions_observed": 25,
  "current_place_name": "บ้าน",
  "predictions": [
    {"rank":1,"place_name":"วัด",  "probability":0.7692,"probability_pct":77},
    {"rank":2,"place_name":"ตลาด","probability":0.2308,"probability_pct":23} ]}
```

**77 / 23 คืออัตราส่วน 10:3 ที่ป้อนเข้าไปเป๊ะ** มันนับจากพฤติกรรมจริง ไม่ใช่เลขที่แต่งขึ้น

- ไม่ส่ง `lat/lng` (ใช้จุดล่าสุด = ตลาด) → **ตลาด → บ้าน 100%** ถูก เพราะจากตลาดกลับบ้านทุกครั้ง
- ยืนนอกรัศมีหมุดทุกจุด → `unknown_current_place`
- ลบทิ้งด้วย `delete_patient --confirm` แล้วเทียบกับ baseline ที่เก็บก่อนเริ่ม:
  **ทุกตารางเท่าเดิมเป๊ะ** (`gps_data 10389 → 10389`, `alerts 88 → 88`, `users 5 → 5`)

### 5.2 เจอว่า Firebase Authentication ไม่มี user เหลืออยู่เลยสักคน

พยายาม sign-in เป็นบัญชีผู้ดูแลทดสอบแล้วไม่ผ่าน ตรวจด้วย Admin SDK พบว่า

```
Admin SDK project_id: pathguard-ai-2d047     ← โปรเจกต์ถูกตัว
firebase user count:  0                      ← ว่างเปล่า
get_user('0v4fv0lf...'): UserNotFoundError
```

แต่ Neon ยังเก็บแถว `users.id = 21` ที่ชี้ไปยัง uid ที่ไม่มีอยู่แล้ว
(สาเหตุ: ถูกลบตอนเก็บกวาด เพราะคิดว่าจะกลายเป็นขยะ — ไม่ใช่บั๊ก)

**ถ้าเปิด `AUTH_ENABLED` ตอนนั้น จะไม่มีใครเข้าระบบได้เลยสักคน** ไม่ใช่แค่ผู้ดูแลคนเดียว
และ **ไม่มีอะไรฟ้องเลย** — แถวใน DB ดูปกติ endpoint ดูปกติ มีแต่การ sign-in จริงเท่านั้นที่ fail

บทเรียนที่บันทึกไว้: `users.firebase_uid` คือ **foreign key ที่ชี้ไปยังระบบที่ไม่มี
referential integrity** ปลายทางหายไปได้โดย Postgres ไม่มีทางรู้

กู้แล้วด้วย `--uid` ที่เพิ่งเพิ่ม — สร้างบัญชีคืนด้วย uid เดิม สคริปต์รายงาน
`reused users row id=21` **ไม่ได้เขียนฐานข้อมูลสักแถว**

**เกี่ยวกับคุณตรงนี้:** หน้า register ของคุณสร้างบัญชีใหม่ได้ปกติ แต่หน้า login
`signInWithEmailAndPassword` จะ fail กับทุกบัญชีที่หายไป

### 5.3 ซ้อมเปิด `AUTH_ENABLED=true` โดยไม่แตะ `.env`

รัน uvicorn ตัวที่สองที่พอร์ต 8001 ด้วย env var — **ท่านี้ทดสอบการเปิด auth ได้โดยไม่ต้องเสี่ยง**

| ทดสอบ | ผล |
|---|---|
| `/api/me` + token จริง — auth **ปิด** | ✅ `id 21 · ผู้ดูแลทดสอบ · caregiver` |
| `/api/me` + token จริง — auth **เปิด** | ✅ ตอบเหมือนกันเป๊ะ |
| `/api/patients/6/track` ไม่มี token | ✅ `401 missing bearer token` |
| ผู้ดูแล 21 ขอดูคนไข้ 6 ที่ไม่ใช่ของตัวเอง | ✅ `403 not your patient` ไม่ใช่ 404 |
| `POST /api/pair` ไม่มี token | ✅ ยังเปิดอยู่ตามที่ออกแบบ |

---

## 6. `AUTH_ENABLED` — เหลือเรื่องเดียว

เหตุผลที่ล็อกไว้เมื่อ 30 ส.ค. คือ *"ฝั่งผู้ดูแลไม่มี Firebase Auth เลย"* — `a96a841`
ปิดข้อนั้นไปแล้ว register / login / pairing ออก token จริงทั้งหมด และ `api_client.dart`
แนบ `Bearer` ทุก request

**เหลือแค่ `caregiver_login_screen.dart:110` ที่ยังอ่าน `CAREGIVER_TEST_ID` จาก `.env`
แทนที่จะเรียก `GET /api/me`** ถ้าเปิด auth ก่อนแก้ตรงนี้ ผู้ดูแลทุกคนที่ login ซ้ำจะยังเป็น
account ทดสอบอยู่ดี แค่ตอนนี้มี token ยืนยันว่าเป็นจริง ๆ

**เราจะบอกล่วงหน้าก่อนเปิดเสมอ ไม่เปิดเงียบ ๆ**

---

## 7. เหลืออะไร

**ฝั่ง backend ไม่เหลืออะไรที่ทำเองได้แล้ว** ทุกอย่างที่ค้างรอฝั่งแอป

| | ใคร | สถานะ |
|---|---|---|
| `AUTH_ENABLED=true` | เรา | ⏸ ซ้อมผ่านแล้ว รอ `/api/me` ฝั่งแอป |
| Cloudflare Tunnel | เรา | ⏸ ทำได้ทันทีหลังเปิด auth |
| บั๊ก 6 ข้อ + เปลี่ยนไปใช้ `/api/me` | คุณ | ❌ |
| FCM + background location | คุณ | ❌ **W2 กับ W3 ติดที่นี่** |
| TrackScreen ยัง mock, ไม่เรียก `/alerts` | คุณ | ❌ **ผู้ดูแลยังไม่เห็นอะไรเลย** |
| 4 ช่องว่าง (Module 4, danger zone, ปิด alert, invite) | คุณ | ❌ |
| สมัครบัญชีผู้ดูแลจริงผ่านหน้า register | คุณ | ❌ เส้นนี้ยังไม่เคยรันจริง |

### ลำดับที่แนะนำ

1. แก้ typo `ANDROID_GOOGLE_MAPS_API_KEYS` — หนึ่งตัวอักษร ปลด Safe Zone ทั้งฟีเจอร์
2. เปลี่ยน login ไปใช้ `GET /api/me` — ปลดล็อก `AUTH_ENABLED` และแก้เรื่อง id ผิดคน
3. ต่อ `GET .../track` + `GET .../alerts` — **ไม่ต้องรออะไรเลย ทำได้ทันที**
   และเป็นส่วนที่ผู้ดูแลได้เห็นของจริงครั้งแรก
4. heartbeat GPS 60 วิ — กัน `gps_lost` หลอกก่อนข้อ 3 จะเริ่มอ่านคะแนน
5. FCM + `POST /api/devices/token` — W3 ขึ้นกับข้อนี้ล้วน ๆ
6. background location + foreground service — W2 ขึ้นกับข้อนี้
7. `PATCH /api/trip-requests/{id}` + เช็ค `patientId` null

> 🛑 **ห้าม poll `GET /api/risk/{id}` และ `GET /api/search-area/{id}`**
> สองตัวนี้เป็น `GET` ที่ **มี side effect** เขียนแถวลง DB และ push ขึ้นมือถือได้
> `search-area` รัน Monte Carlo 10,000 รอบ คืนราว 2,500 cell ต่อครั้ง
> ใช้ `/track` กับ `/alerts` แทน สองตัวนั้นอ่านอย่างเดียว

---

## 8. เอกสารที่เกี่ยวข้อง

| ไฟล์ | อะไร |
|---|---|
| `backend/APP_SYNC_2026-09-01.md` | **งานที่ต้องทำ พร้อมโค้ด Dart ร่างไว้ให้** — อ่านไฟล์นี้ก่อน |
| `backend/API_CONTRACT_APP.md` §14 | `GET /api/me` |
| `backend/API_CONTRACT_APP.md` §15 | `GET /api/predict-destination/{id}` |
| `README.md` | ตาราง endpoint ทั้ง 35 ตัว generate จาก `app.openapi()` |

⚠️ โค้ด Dart ทุกบล็อกในเอกสารพวกนี้ **ไม่เคยถูกคอมไพล์** เครื่องฝั่ง backend ไม่มี Flutter
ถือเป็นร่างให้ปรับ ไม่ใช่โค้ดที่พร้อมวาง
