# PathGuard AI

GPS-based wandering detection system for dementia patients.
**Backend:** Python / FastAPI + Firebase + PostgreSQL · **Mobile:** Flutter

---

## Setup (every teammate must do this after cloning)

Installed packages and secrets are **not** in git — each person sets them up locally.

### 1. Install dependencies (run once)
```bash
pip install -r backend/requirements.txt
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

**How startup wires in (for `main.py`):**
```python
from app.db.database import get_db, init_db, init_firebase

init_firebase()      # on startup
await init_db()      # creates tables

async def handler(db: AsyncSession = Depends(get_db)):   # in an endpoint
    ...
```

**Note for whoever does deletes/retention:** FK cascade is ORM-level (`cascade="all, delete-orphan"`). Deleting a `User` via SQLAlchemy cascades to their rows; a *raw SQL* delete is blocked by the FK. Add `ondelete="CASCADE"` to the FK columns if you need DB-level cascade.

---

## Remaining (other roles)
- `main.py` — wire startup (`init_firebase()` + `init_db()`) and routers
- `services/notification.py`
- `api/` — gps (calls `gps_processor.process_gps_point`), risk, search_area, recommendation endpoints
- AI modules 1–5 (behavior, prediction, risk, search area, recommend) — read/write via `crud.py`
- Flutter `location_service.dart`
