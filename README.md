# PathGuard AI

GPS-based wandering detection system for dementia patients.
**Backend:** Python / FastAPI + Firebase + PostgreSQL · **Mobile:** Flutter

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
`tensorflow` (Module 1 LSTM only) is heavy; if you're not on that module you can
install everything else and skip it — the rest of the backend runs without it.

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
Endpoints live today: `POST /api/register`, `POST /api/gps`, `GET /`.

---

## Latest additions (DB side) — ✅ done, on `feature/database`

| Item | Where | Notes |
|------|-------|-------|
| **`POST /api/register`** | `app/api/users.py` | Creates a `users` row from `firebase_uid`; returns the int `users.id`. 201 created · 409 duplicate uid · 422 bad role. Verified over real HTTP. |
| **`firebase_uid → users.id` lookup** | `crud.get_user_id_by_firebase_uid` | Resolves the Flutter string UID to the int FK before writing GPS/AI data. |
| **`create_user`** | `crud.create_user` | Insert helper (flush; caller owns the tx). |
| **`direction` + `device_motion` columns** | `models.GPSData`, `gps_data` table (live), `GPSDataCreate/Response` | Stored end-to-end. Module 2 (wandering) needs `direction`. |
| **Module 1 ↔ DB connector** | `app/ai/module1_behavior/behavior_pipeline.py` | `analyze_behavior(db, patient_id)`: reads GPS history → preprocess + cluster → writes `behavioral_profiles`. Verified against Neon. |
| **App entry point** | `app/main.py` | Minimal FastAPI app; mounts users + gps. |

Key team decisions affecting GPS ingestion (int `patient_id` FK, UTC timestamps, stored `direction`/`device_motion`) are folded into the **GPS endpoint owner** task below.

---

## What each teammate should do next

- **GPS endpoint owner** — **TODO (not done yet):** `api/gps.py` currently
  persists GPS to an in-memory list (`data_collection.save_gps_data`), so nothing
  reaches Postgres and the AI modules have no history to read. Repoint it to the
  real single-writer path, `gps_processor.process_gps_point()` (Kalman-smooths →
  writes Postgres → pushes live position to Firebase):
  ```python
  # app/api/gps.py
  from fastapi import APIRouter, Depends
  from sqlalchemy.ext.asyncio import AsyncSession

  from app.db.database import get_db
  from app.models.gps_data import GPSDataCreate
  from app.services.gps_processor import process_gps_point

  router = APIRouter()

  @router.post("/api/gps")
  async def receive_gps(data: GPSDataCreate, db: AsyncSession = Depends(get_db)):
      point = await process_gps_point(db, data)
      return {"status": "success", "patient_id": data.patient_id, "id": point.id}
  ```
  Reconcile the request model with `GPSDataCreate` (`app/models/gps_data.py`):
  `patient_id` is an **int** FK to `users.id` (not the Flutter string UID — that
  lives in `users.firebase_uid`; resolve it via `crud.get_user_id_by_firebase_uid`);
  the time field is `recorded_at: datetime` (parse the app's UTC ISO string ending
  in `Z`; server stamps `datetime.now(timezone.utc)` if missing); `accuracy` and
  `altitude` are optional. Once repointed, `analyze_behavior(db, patient_id)` runs
  the Module 1 pipeline with no further DB work.
- **Module 1 (behavior) owner** — call `analyze_behavior(db, patient_id)` from a
  trigger/endpoint when you want to (re)learn places. Also tune the Kalman params
  in `preprocess_gps` (`R=1e-3 ≫ Q=1e-5`): they currently smooth across location
  jumps, so distinct places ~1 km apart collapse into one cluster.
- **Module 2–5 owners** — create your router in `api/` and mount it in
  `app/main.py` at the marked `TODO (teammates)` line. Read/write data **only**
  through `crud.py`, never raw SQL.
- **Flutter dev** — after Firebase sign-in, call `POST /api/register` once so the
  `users` row exists before any GPS is sent. Send timestamps as **UTC ISO**
  strings ending in `Z`.
- **Still unimplemented:** `services/notification.py`, AI modules 2–5, Flutter
  `location_service.dart`.
