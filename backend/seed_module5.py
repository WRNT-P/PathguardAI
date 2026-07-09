# pathguard/backend/seed_module5.py
"""Thin shim: the seeder now lives in app/mock/seed_module5.py.

Kept so the old command still works from the backend/ directory:
    python seed_module5.py
"""
import asyncio

from app.mock.seed_module5 import main

if __name__ == "__main__":
    asyncio.run(main())
