# pathguard/backend/app/api/gps.py

from fastapi import APIRouter
from app.ai.module1_behavior.data_collection import save_gps_data
from app.ai.module1_behavior.data_preprocessing import preprocess_gps

router = APIRouter()

@router.post("/api/gps")
async def receive_gps(data: dict):
    """
    รับข้อมูล GPS จาก Flutter
    """
    # Step 1.1 — เก็บข้อมูลลง Database
    save_gps_data(data)

    return {"status": "success"}