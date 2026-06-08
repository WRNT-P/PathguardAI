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

### 3. Get `backend/serviceAccountKey.json`
Firebase service-account key. **Not in git** (it's a secret) — ask a teammate to share it privately. Never push it.

---

## Database Layer — Status: ✅ Done

The database layer is fully implemented and import-tested.

| File | What it does |
|------|--------------|
| `backend/app/db/database.py` | Firebase Admin SDK init + PostgreSQL async engine. Exposes `get_db` (FastAPI dependency), `init_db()`, `init_firebase()`, `get_firebase_ref()` |
| `backend/app/db/models.py` | SQLAlchemy ORM tables: **User, GPSData, RiskScore, Alert, BehavioralProfile** |
| `backend/app/models/*.py` | Pydantic request/response schemas for GPS, user, alert, risk score |

**Data split:**
- **Firebase Realtime DB** → live GPS position, alerts, chat
- **PostgreSQL** → GPS history (30 days), behavioral profiles, risk scores, AI data

**How other modules use it:**
```python
from app.db.database import get_db, init_db, init_firebase

# on startup (in main.py)
init_firebase()
await init_db()

# in an endpoint
async def handler(db: AsyncSession = Depends(get_db)):
    ...
```

---

## Remaining (other roles)
- `main.py` — wire startup (`init_firebase()` + `init_db()`) and routers
- `services/` — kalman_filter, gps_processor, firebase, notification
- `api/` — gps, risk, search_area, recommendation endpoints
- AI modules 1–5 (behavior, prediction, risk, search area, recommend)
- Flutter `location_service.dart`
