# pathguard/backend/app/ai/module1_behavior/data_collection.py

# TODO: เชื่อม Database เมื่อคนที่ 2 เขียน get_db() เสร็จ
# from app.db.database import get_db

# เก็บข้อมูลใน list แทน Database ไปก่อน
gps_storage = []

def save_gps_data(data: dict):
    gps_storage.append(data)
    print(f"บันทึกแล้ว ✅ ตอนนี้มีข้อมูล {len(gps_storage)} รายการ")
    print(gps_storage)

def get_all_gps(patient_id: str):
    return [d for d in gps_storage if d["patient_id"] == patient_id]