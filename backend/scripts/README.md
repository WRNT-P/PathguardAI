# Real-data pipeline (GeoLife → AI Modules 1–5)

Loads real GPS data into PostgreSQL and drives the existing AI (Modules 1–5) on
it, ending in a terminal demo. **No AI module logic, rules, weights, or
thresholds are changed** — this is the data/DB layer only.

Base data = Microsoft **GeoLife** (real human GPS). Domain behaviour (wandering)
does not exist in GeoLife, so a synthetic **pacing** episode is injected on top,
flagged `synthetic_injected=True` so real vs injected stays auditable. See
GitHub issue #1 for the full rationale.

## Prerequisites (once)

```bash
cd backend
python -m venv venv && venv/Scripts/pip install -r requirements.txt   # Windows
# .env must contain DATABASE_URL=postgresql+asyncpg://...   (already set)
```

Download **GeoLife GPS Trajectories 1.3** (Microsoft Research) and extract so the
per-user folders sit at:

```
backend/data/Geolife/Data/025/Trajectory/*.plt
```

(`backend/data/` is gitignored — the dataset is not committed.)

> On Windows, prefix commands with `PYTHONIOENCODING=utf-8` if the console
> mangles non-ASCII output.

## Run order

Run from `backend/`, each is idempotent (safe to re-run).

| # | Command | What it does |
|---|---------|--------------|
| 0 | `python -m scripts.migrate_add_synthetic_injected` | add the `synthetic_injected` column (no alembic) |
| 0 | `python -m app.mock.seed_risk_rules` | seed Module 3 rule KB (weights/thresholds/danger zones) |
| 1 | `python -m scripts.import_geolife --user 025` | parse `.plt`, derive speed/direction, remap timestamps to the recent 30-day window, downsample, load into `gps_data` (creates a patient user) |
| 2 | *(build profile)* — done inside the demo, or call `analyze_behavior` directly | Module 1 clusters known places from the **real** history (before any injection) |
| 3 | `python -m scripts.inject_wandering --patient <id>` | inject a pacing+dwell wandering episode + a co-located seeded danger zone (all `synthetic_injected=True`) into the recent window |

`import_geolife` prints the created patient id (e.g. `13`). Use it below.

## Demo

```bash
python -m scripts.demo_run --patient 13
```

Re-injects a fresh episode (so the "current session" is always recent), then
prints a 5-module narrative: learned routine → wandering detected → risk score +
emergency → search area → ranked places to check.

- **TF-free.** Module 2's LSTM destination path is skipped (needs TensorFlow);
  its sklearn wandering detector — the heart of the demo — runs.
- `--no-fresh` skips the re-inject (use existing DB state).

## Tests

```bash
python -m pytest tests/test_import_geolife.py tests/test_phase4_integration.py -q
```

`test_phase4_integration.py` drives the whole pipeline on in-memory SQLite (no
TensorFlow, no GeoLife files) and asserts risk > 80 + emergency, traceable to the
injected segment.
