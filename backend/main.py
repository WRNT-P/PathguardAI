# pathguard/backend/main.py
# Thin launcher so `uvicorn main:app` works from backend/.
# The real app (routers, startup) lives in app/main.py — single source of truth.
from app.main import app  # noqa: F401
