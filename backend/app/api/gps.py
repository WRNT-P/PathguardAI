# pathguard/backend/app/api/gps.py

from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.ai.module1_behavior.data_collection import save_gps_data

router = APIRouter()


class GPSData(BaseModel):
    """
    Schema สำหรับข้อมูล GPS ที่รับจาก Flutter app
    """
    patient_id: str = Field(..., description="รหัสผู้ป่วย")
    latitude: float = Field(..., ge=-90, le=90, description="ละติจูด")
    longitude: float = Field(..., ge=-180, le=180, description="ลองจิจูด")
    speed: float = Field(default=0.0, ge=0, description="ความเร็ว (m/s)")
    direction: float = Field(default=0.0, ge=0, lt=360, description="ทิศทาง (องศา 0-359)")
    device_motion: str = Field(default="unknown", description="สถานะการเคลื่อนที่ของอุปกรณ์")
    timestamp: str = Field(..., description="เวลา ISO 8601 เช่น 2026-06-12T13:00:00Z")


@router.post("/api/gps", summary="รับข้อมูล GPS จาก Flutter app")
async def receive_gps(data: GPSData):
    save_gps_data(data.model_dump())
    return {"status": "success", "patient_id": data.patient_id}