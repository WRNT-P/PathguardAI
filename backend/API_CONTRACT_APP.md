# API contract — แอปมือถือ: รับแจ้งเตือน (FCM)

> เขียน 2026-08-22 · สำหรับคนที่ 1 (Flutter)
> **สถานะ: backend เขียนเสร็จและมีเทสต์แล้ว** — 14 เทสต์ผ่าน ยิงจริงได้เลย
> ถ้าจะเปลี่ยนอะไรในนี้ ต้องบอกกันก่อน อย่าแก้ฝ่ายเดียว

ก่อนหน้านี้เวลาผู้ป่วยเดินหลง ระบบเขียนแถวลงตาราง `alerts` แล้วจบ ไม่มีอะไรออกจากเซิร์ฟเวอร์
วิธีเดียวที่จะรู้คือต้องนั่งเฝ้า dashboard เอาไว้ ตอนนี้ต่อสายสุดท้ายแล้ว

---

# 1. ลงทะเบียนเครื่องผู้ดูแล

## `POST /api/devices/token`

**ถ้าไม่เรียกอันนี้ = ไม่มี push ตลอดกาล** alert จะถูกเขียนลง DB แล้วไม่ไปไหน

### Request

```json
{
  "user_id": 12,
  "token": "fcm-registration-token-จาก-firebase-messaging",
  "platform": "android"
}
```

| ฟิลด์ | ชนิด | หมายเหตุ |
|---|---|---|
| `user_id` | int | `users.id` ของ **ผู้ดูแล** ไม่ใช่ของผู้ป่วย — ได้จาก `POST /api/register` |
| `token` | string 10–255 | จาก `FirebaseMessaging.instance.getToken()` |
| `platform` | `android` \| `ios` \| `web` | default `android` |

### Response `200`

```json
{ "id": 1, "user_id": 12, "platform": "android" }
```

`404` = ยังไม่ได้ `POST /api/register` ก่อน

### เรียกเมื่อไหร่

- หลัง sign-in ครั้งแรก
- **ทุกครั้งที่เปิดแอป** — Firebase ออก token ใหม่ตอนลงแอปใหม่ และหมดอายุเองได้
- ตอน `onTokenRefresh` ยิง

เรียกซ้ำได้ ไม่เกิดแถวซ้ำ (upsert ยึด token เป็นตัวตน) และถ้าเครื่องเดียวกันมีคนล็อกอินใหม่
token จะย้ายไปเป็นของคนหลังสุดโดยอัตโนมัติ

---

# 2. ผูกผู้ป่วยกับผู้ดูแล

push วิ่งตาม `users.caregiver_id` ที่มีอยู่ใน schema แล้ว **ไม่มีรหัสเชิญ ไม่มีตารางจับคู่**
ตอนสมัครผู้ป่วย ส่ง `caregiver_id` มาด้วย:

```json
POST /api/register
{ "firebase_uid": "...", "name": "ยาย", "role": "patient", "caregiver_id": 12 }
```

ผู้ป่วยที่ไม่มี `caregiver_id` จะไม่มีวันได้รับ push — backend เขียน log warning ไว้ แต่ไม่ error

---

# 3. หน้าตาของ push ที่จะได้รับ

### `notification`

| ฟิลด์ | ค่า |
|---|---|
| `title` | `PathGuard — เข้าเขตอันตราย` / `— ต้องการความช่วยเหลือ` / `— สัญญาณ GPS หาย` / `— พบการเดินหลง` |
| `body` | ข้อความจาก backend เช่น `Patient entered a danger zone — risk 91%.` |

### `data` — **ทุกค่าเป็น string** (ข้อบังคับของ FCM ต้อง parse เอง)

```json
{
  "alert_id": "42",
  "patient_id": "7",
  "alert_type": "geofence",
  "severity": "critical",
  "latitude": "13.77",
  "longitude": "100.555"
}
```

| ฟิลด์ | ใช้ทำอะไร |
|---|---|
| `patient_id` | เอาไปเปิดหน้าแผนที่ของผู้ป่วยคนนั้น |
| `latitude` / `longitude` | จุดที่เกิดเหตุ — **อาจเป็นสตริงว่าง `""`** ถ้า alert นั้นไม่มีพิกัด ต้องเช็คก่อน parse |
| `alert_type` | `geofence` (เข้าเขตอันตราย) · `emergency` (คะแนนสูง/เสี่ยงต่อเนื่อง) · `gps_loss` / `gps_lost` (สัญญาณหาย) · `wandering` |
| `severity` | `low` · `medium` · `high` · `critical` |

`android.priority = "high"` ตั้งไว้แล้วฝั่ง backend เพื่อให้ทะลุ Doze ของ Android

---

# 4. จะได้ push ถี่แค่ไหน

**อย่างมาก 1 ครั้งต่อ 10 นาที ต่อ `alert_type` ต่อผู้ป่วย 1 คน**

ตาราง `alerts` ยังเขียนทุกรอบที่เข้าเงื่อนไข (รอบละ 60 วิ ตอน GPS เข้า) เพราะมันคือประวัติ
ที่หน้า timeline ต้องใช้ แต่การ *ส่ง* ถูกจำกัดแยกต่างหาก — ยายที่นั่งอยู่ในเขตอันตราย 1 ชั่วโมง
ได้ alert 60 แถว แต่ได้ push 6 ครั้ง ไม่ใช่ 60

`alert_type` ต่างชนิดไม่กด cooldown ของกันและกัน — GPS หายตอนที่เพิ่งส่ง geofence ไป
30 วินาทีก่อน ยังส่งได้ตามปกติ

ค่า 10 นาทีนี้อยู่ในตาราง `risk_thresholds` ชื่อ `push_cooldown_seconds` แก้ได้ตอนรันโดยไม่ต้อง deploy
(อ้างอิง MOPH ED Triage 2561 Level 1-2 = แทรกแซงใน 0–10 นาที)

---

# 5. เกณฑ์ที่ทำให้เกิด alert (ไม่ได้เปลี่ยน)

ทั้งหมดมาจาก MOPH ED Triage 2561 ที่เก็บใน rule KB — backend ไม่ได้ตั้งเกณฑ์ใหม่เพื่อ push

| เกิด alert เมื่อ | ผลลัพธ์ |
|---|---|
| อยู่ในเขตอันตราย | `geofence` · `critical` · ทันที |
| คะแนน > 80 | `emergency` · `high` · ทันที |
| คะแนน ≥ 50 ติดกัน 5 รอบ | `emergency` · `high` · reason `sustained_risk` |
| ไม่มี GPS เกิน 10 นาที | `gps_loss` · `high` |

> ⚠️ **สิ่งที่ต้องรู้:** เดินหลงห่างบ้าน 2 กม. วัดได้ **63.5 = medium ไม่ถึง 80** เคสเดินหลงจริง
> จึงมาทางกฎ "5 รอบติด" เสมอ = ~5 นาทีหลังเริ่มหลง ไม่ใช่ทันที
