# PathGuard AI

GPS-based wandering detection system for dementia patients.
**Backend:** Python / FastAPI + Firebase + PostgreSQL · **Mobile:** Flutter

---

## 📌 อ่านก่อน — เอกสารกลางของทีม

| ไฟล์ | ใช้ตอนไหน |
|---|---|
| **`backend/REPORT_VS_CODE.md`** | **รายงานอ้างอะไรไว้ แล้ว backend ทำได้จริงแค่ไหน** อ่านก่อนสร้างฟีเจอร์ตามรายงาน มีคำถามที่รอฝั่งแอปตอบอยู่ท้ายไฟล์ |
| `backend/API_CONTRACT_APP.md` | สัญญาสำหรับแอปมือถือ — register, จับคู่เครื่อง, GPS, FCM token, push payload, การอ่าน track/alert, SOS, ขออนุมัติเดินทาง, **ปักหมุดสถานที่ (§10)** |
| `backend/API_CONTRACT_ADMIN.md` | สัญญาสำหรับหน้าผู้ดูแล — ปักหมุดสถานที่ (`places`) และเขตอันตราย |

> เอกสารของโปรเจกต์เขียนคนละเวลา และโค้ดฝั่งแอปกับ backend อยู่คนละรีโป **ถ้าเจอที่ไม่ตรงกัน
> ให้ยึดโค้ดกับสามไฟล์นี้ แล้วบอกกันทันที** — สามครั้งที่ผ่านมาเสียเวลาไปกับการสร้างของที่อีกฝั่ง
> ตัดไปแล้ว หรือรอของที่อีกฝั่งไม่รู้ว่าต้องทำ

---

## Setup (every teammate must do this after cloning)

Installed packages and secrets are **not** in git — each person sets them up locally.

### 1. Install dependencies (run once)
Recommended: use a virtual environment so deps stay isolated.
```bash
python -m venv backend/venv      # create once
# activate — Windows: backend\venv\Scripts\activate  |  macOS/Linux: source backend/venv/bin/activate
pip install -r backend/requirements.txt
```
`tensorflow` is **commented out in `requirements.txt` and not installed** — the
4-week plan cut the Module 2 LSTM (section 08) and `app/main.py` does not mount
the router that needs it. Everything else, including the Isolation Forest and
Markov parts of Module 2, runs without it. Do not uncomment it unless you are
deliberately bringing the LSTM back; see the docstring at the top of `app/main.py`.

To also run the test suite, install the dev deps too:
```bash
pip install -r backend/requirements-dev.txt
```

### 2. Create `backend/.env` (not in git — make it yourself)
```env
FIREBASE_CREDENTIALS_PATH=./serviceAccountKey.json
FIREBASE_DATABASE_URL=https://<your-project>.firebaseio.com
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/pathguard
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=True
```

**Database options for `DATABASE_URL`:**
- **Cloud (recommended for the team):** a free [Neon](https://neon.tech) Postgres project — everyone shares one DB. Paste Neon's connection string directly; `database.py` auto-adapts it (handles the `+asyncpg` driver and the `sslmode`/`channel_binding` SSL params), so the raw `postgresql://…?sslmode=require` string works as-is.
- **Local:** your own PostgreSQL on `localhost:5432`.

Tables are created automatically by `init_db()` on first startup — no manual SQL needed.

### 3. Get `backend/serviceAccountKey.json`
Firebase service-account key. **Not in git** (it's a secret) — ask a teammate to share it privately. Never push it.

---

## Database + GPS Ingestion Layer — Status: ✅ Done & verified

Implemented and **verified end-to-end against cloud Postgres (Neon)**: connect → `init_db()` creates all 5 tables → write through every `crud.py` helper → read back → all correct.

| File | What it does |
|------|--------------|
| `backend/app/db/database.py` | Firebase Admin SDK init + PostgreSQL async engine. Exposes `get_db` (FastAPI dependency), `init_db()`, `init_firebase()`, `get_firebase_ref()`. Auto-adapts a raw Neon/libpq URL for the asyncpg driver. |
| `backend/app/db/models.py` | SQLAlchemy ORM tables: **User, GPSData, RiskScore, Alert, BehavioralProfile** |
| `backend/app/models/*.py` | Pydantic request/response schemas for GPS, user, alert, risk score |
| `backend/app/db/crud.py` | **Data-access API the AI modules build on** — async repository helpers (see below) |
| `backend/app/services/kalman_filter.py` | 2D constant-velocity Kalman filter, one per patient, smooths jittery GPS |
| `backend/app/services/gps_processor.py` | **The single writer of GPS data:** smooth → persist to Postgres → push live position to Firebase |
| `backend/app/services/firebase.py` | Writes live position to Firebase Realtime DB |

**Data split:**
- **Firebase Realtime DB** → live GPS position, alerts, chat
- **PostgreSQL** → GPS history (30 days), behavioral profiles, risk scores, AI data

### How the AI modules read/write data (use `crud.py`, don't write raw SQL)
```python
from app.db import crud

# reads (AI modules 1–5)
history = await crud.get_gps_history(db, patient_id, days=30)   # behavior clustering input
latest  = await crud.get_latest_gps(db, patient_id)
profile = await crud.get_behavioral_profile(db, patient_id)
risk    = await crud.get_latest_risk_score(db, patient_id)

# writes
await crud.save_risk_score(db, patient_id, score, level, wandering_detected=...)
await crud.save_alert(db, patient_id, alert_type, severity, message)
await crud.upsert_behavioral_profile(db, patient_id, known_places=..., routine_patterns=...)
```
**Transaction rule:** `crud` helpers `flush` but never `commit` — the request owns the transaction (`get_db` commits at the end). GPS history is written **only** through `gps_processor.process_gps_point()`, never `crud.save_gps_point` directly.

**Startup is wired in `app/main.py`** (now implemented). Its lifespan calls
`await init_db()` to create tables. `init_firebase()` is **not** called yet — it
needs `serviceAccountKey.json`, and the live GPS push is best-effort, so the API
runs without it during development. Add `init_firebase()` to the lifespan once
everyone has the key.

**Note for whoever does deletes/retention:** FK cascade is ORM-level (`cascade="all, delete-orphan"`). Deleting a `User` via SQLAlchemy cascades to their rows; a *raw SQL* delete is blocked by the FK. Add `ondelete="CASCADE"` to the FK columns if you need DB-level cascade.

---

## Running the backend
```bash
# from backend/ with the venv activated
uvicorn app.main:app --reload
```
Then open **http://127.0.0.1:8000/docs** for the interactive API docs.

Endpoints live today — **35 routes, listed straight off `app.openapi()`** so this
table cannot drift from what the process actually serves:

| Method | Path | What |
|---|---|---|
| POST | `/api/register` | create the `users` row for a signed-in Firebase account |
| GET | `/api/me` | who this bearer token belongs to — `users.id`, `role`, `phone` |
| POST | `/api/patients` | caregiver creates a patient, gets an 8-char pairing code |
| POST | `/api/pair` | the patient's phone trades that code for a Firebase custom token |
| GET | `/api/patients/{id}` | patient name + `severity_level` — the phone reading its own stage |
| POST | `/api/gps`, `/api/gps/batch` | GPS ingestion — Kalman → Postgres → Firebase, then risk scoring |
| POST | `/api/sos` | patient pressed the button — skips scoring entirely |
| GET | `/api/risk/{id}` | Module 3 — risk score 0–100 ⚠️ **has side effects, never poll** |
| GET | `/api/search-area/{id}` | Module 4 — Monte Carlo + KDE ⚠️ **has side effects, never poll** |
| GET | `/api/recommendation/{id}` | Module 5 — where the patient likely wants to go |
| GET | `/api/predict-destination/{id}` | Module 2 — next place, from the Markov transition matrix. Read `scorer` and `history_status` before displaying a percentage |
| POST/GET | `/api/patients/{id}/places` | caregiver-pinned places (whole set) |
| PUT | `/api/patients/{id}/places/home` | upsert just the home pin, without wiping the rest |
| GET | `/api/patients/{id}/track` | recent GPS track for the map |
| GET | `/api/patients/{id}/alerts` · PATCH `/api/alerts/{id}` | alert feed + mark resolved |
| POST/DELETE | `/api/alerts/{id}/claim` | "I'll go and get them", and releasing it again |
| GET | `/api/patients/{id}/caregivers` | caregivers ranked by distance to the patient |
| POST | `/api/patients/{id}/caregiver-invites` | invite a second caregiver to this patient |
| POST | `/api/caregivers/redeem-invite` | the second caregiver redeems that code |
| PUT | `/api/caregivers/{id}/location` | the caregiver app reports where it is (latest only) |
| POST | `/api/devices/token` | FCM device token — see `backend/API_CONTRACT_APP.md` |
| POST | `/api/trip-requests` · GET `/api/patients/{id}/trip-requests` · PATCH `/api/trip-requests/{id}` | C-3 trip approval |
| POST/GET/DELETE | `/api/danger-zones` | danger zone admin (DELETE is a soft deactivate) |
| GET | `/api/admin/rules`, `/api/admin/rules/history` | rule knowledge-base — **read-only, both are GETs** |
| GET | `/`, `/health` | service info, liveness probe |

> **`/api/predict-destination` is served by `app/api/destination.py`, NOT by the
> LSTM.** The LSTM destination model was cut by the 4-week plan (section 08);
> `app/api/prediction.py` is still in the repo, is not mounted, and **must not be**
> — its import chain reaches `lstm_utils.py:6 import tensorflow` at module scope,
> so mounting it without installing TensorFlow first stops the **whole application**
> from booting, and its path is now taken. Read the docstrings at the top of
> `app/main.py` and `app/api/prediction.py` before touching either.
>
> The cut was the LSTM alone, not Module 2. `wandering_detection` (Isolation
> Forest), `route_prediction` (Markov + Viterbi) and `stop_confusion_classification`
> run on every GPS point through `app/ai/module3_risk/risk_data_collection.py` and
> carry 0.25 + 0.30 + 0.20 of the risk weights between them.


---

## AI Core Engine (Modules 1–5) — ✅ implemented & tested

All 5 AI modules described in the architecture doc are implemented under
`backend/app/ai/` and wired into the API above. Verified with the full test
suite (`python -m pytest -q` from `backend/`, 419 tests passing) plus an
end-to-end integration test (`tests/test_phase4_integration.py`) that drives
Module 1 → 2 → 3 → 4 → 5 back to back and asserts a high-risk emergency is
correctly triggered and traced back to an injected wandering episode.

See `backend/scripts/README.md` for a runnable demo against real GeoLife GPS
data (`python -m scripts.demo_run --patient <id>`).

### Running the tests
```bash
cd backend
python -m pytest -q
```
No PostgreSQL or Firebase needed — the suite runs against an in-memory SQLite
DB (see `tests/conftest.py`). Requires `requirements-dev.txt` installed (step 1
above).

---

## What each teammate should do next

- **GPS endpoint owner — ✅ done 2026-08-19.** `POST /api/gps` calls
  `gps_processor.process_gps_point()` (Kalman-smooths → writes Postgres → pushes
  live position to Firebase), and `POST /api/gps/batch` sorts by `recorded_at`
  before feeding the filter. Note the request shape: `patient_id` is an **int** FK
  to `users.id` (not the Firebase UID string — that lives in `users.firebase_uid`),
  the time field is `recorded_at` (UTC ISO ending in `Z`), and `speed`/`direction`
  default to `null`. Full contract in `backend/API_CONTRACT_APP.md`.
- **Module 1 (behavior) — ⛔ ห้ามเรียก `analyze_behavior()` (ตัดสิน 2026-08-26).**
  วัดแล้วว่า DBSCAN บนจุด GPS ดิบให้ "สถานที่" 142–156 แห่งต่อ 30 วัน ก้อนใหญ่สุดกินพื้นที่
  1.5 กม. จากจุดศูนย์กลางตัวเอง และ `cluster_places` ไม่ใส่ชื่อสถานที่มาให้เลย — สถานที่ที่
  ระบบเรียนรู้เองจึงบอกครอบครัวเป็นคำพูดไม่ได้ **production ใช้หมุดที่ผู้ดูแลปักเท่านั้น**
  (`POST /api/patients/{id}/places`) โค้ดยังอยู่ในรีโปเพื่อใช้เป็นผลการทดลองในรายงาน
- **Flutter dev** — on every *later* sign-in call `GET /api/me` to get that same
  `users.id` back, plus `role` and `phone`. Do not cache the id in a config file:
  it is per-account, and a hardcoded one silently files every caregiver's patients
  under somebody else's row. Contract §14.
- **Flutter dev** — after Firebase sign-in, call `POST /api/register` once so the
  `users` row exists before any GPS is sent. Send timestamps as **UTC ISO**
  strings ending in `Z`. Then `POST /api/devices/token` with the caregiver's FCM
  token, or alerts are written and never delivered — full contract and push
  payload in `backend/API_CONTRACT_APP.md`. หน้าผู้ดูแลที่เพิ่มผู้ป่วยต้องส่ง
  **สถานที่ที่ผู้ป่วยไปเป็นกิจวัตรให้ครบ ไม่ใช่แค่บ้าน** — วัดแล้วว่าปักแค่บ้านทำให้วัด ตลาด
  และบ้านลูกหลานได้คะแนนเสี่ยง 56 (medium) เท่ากับหลงห่างบ้าน 2.5 กม. ทุกครั้งที่ไป
  **สัญญาอยู่ที่ `backend/API_CONTRACT_APP.md` §10 แล้ว** (เดิมอยู่แต่ใน `API_CONTRACT_ADMIN.md`
  ซึ่งไม่เคยส่งให้ฝั่งแอป) หน้าจอเพิ่มผู้ป่วยส่งบ้านจุดเดียวด้วย
  `PUT /api/patients/{id}/places/home` ซึ่ง**ลบหมุดอื่นไม่ได้** ส่วน
  `POST /api/patients/{id}/places` ทับทั้งชุด — ใช้ผิดตัวแล้วหมุดหายเงียบ ๆ พร้อม `201`
  และ `POST /api/sos` ก็พร้อมใช้แล้ว
- **Module 5 (recommendation) — `time_match` มีข้อมูลแล้วตั้งแต่ 2026-08-26.**
  `behavioral_profiles.routine_patterns` ไม่เคยมีคนเขียนมาก่อน ทำให้ตัวจัดอันดับวิ่งบน 3
  ปัจจัยจาก 4 ตอนนี้มี `python -m scripts.build_routine_patterns` เป็นคนเขียน — มันเรียนแค่
  *ช่วงเวลา* ที่ผู้ป่วยอยู่แต่ละหมุด **ไม่ได้เดาสถานที่เอง** ถ้าไม่มีหมุดก็ไม่เรียนอะไรเลย
  ไม่ต้องตั้ง scheduler รันเมื่อผู้ป่วยมีประวัติมากพอก็พอ
- **A patient can have several caregivers (2026-08-28).** The link lives in
  `patient_caregivers`, not in a single `users.caregiver_id` FK. Every caregiver
  linked to a patient has identical access and every one of them is pushed to;
  `is_primary` only marks whoever created the patient. A second caregiver joins by
  redeeming an invite code — a **different** code space from the patient-device
  pairing code, because one claims a patient's identity and the other grants
  access to them. `backend/scripts/migrate_add_patient_caregivers.py` moves an
  existing database over and must be run once.
- **Auth is built but off.** `app/services/auth.py` verifies a Firebase ID token,
  maps it to `users.id`, and allows only the patient or any of their caregivers. It is
  gated behind `AUTH_ENABLED` (default `false`); see `.env.example`. Send the
  `Authorization: Bearer <id token>` header from the start so turning it on is a
  one-line change on the server and none in the app.
- **แอป Flutter ไม่ได้อยู่ในรีโปนี้** — `git ls-files` ไม่มีไฟล์ Flutter สักไฟล์ งานฝั่งแอป
  อยู่ที่อื่น ซึ่งเป็นเหตุผลที่เอกสารสองฝั่งหลุดจากกันได้ง่าย ถ้าจะย้ายเข้ามารวมกัน คุยกันก่อน
