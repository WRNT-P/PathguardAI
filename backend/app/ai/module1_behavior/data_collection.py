# pathguard/backend/app/ai/module1_behavior/data_collection.py

from datetime import datetime
from app.db.database import get_db

def save_gps_data(data: dict):
    """
    รับข้อมูล GPS จาก Flutter แล้วเก็บลง Database
    """
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        INSERT INTO gps_logs 
        (patient_id, timestamp, latitude, longitude, speed, direction, device_motion)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        data["patient_id"],
        data["timestamp"],
        data["latitude"],
        data["longitude"],
        data["speed"],
        data["direction"],
        data["device_motion"]
    ))

    db.commit()
    cursor.close()
    db.close()