# PathGuard AI — Module 3 (Risk) เป็น Rule-Based Expert System

> ✅ **ตรวจกับ `rule_repository.KNOWN_THRESHOLDS` และ `seed_risk_rules.py` เมื่อ 27 ส.ค. 2026**
> เดิมเขียนไว้ 22 ส.ค. และขาดเกณฑ์ตัวที่ 7 คือ `sos_cooldown_seconds` — เพิ่มลงตารางหัวข้อ 5 แล้ว
> 🛑 **`get_all_thresholds` จะไม่ยอมโหลดถ้าเกณฑ์ที่รู้จักขาดไปแม้ตัวเดียว** บนฐานข้อมูลที่ยัง
> ไม่ seed ใหม่ อาการคือ **การคำนวณคะแนนเสี่ยงล้มทั้งระบบ** ไม่ใช่แค่ SOS —
> รัน `python -m app.mock.seed_risk_rules` ก่อน deploy (idempotent ใส่เฉพาะแถวที่ขาด)
> น้ำหนักและแหล่งอ้างอิงทางการแพทย์ในเอกสารนี้ตรงกับโค้ด
> ♻️ **แก้ 2 ก.ย. 2026 — เพิ่มเรื่องที่ไม่เคยอยู่ในเอกสารฉบับไหนเลย:** เกณฑ์ถูกแบ่งเป็น
> **สองชั้น** ไม่ใช่ชั้นเดียว — `KNOWN_THRESHOLDS` 7 ตัว (ขาดตัวใดตัวหนึ่ง = โหลดไม่ผ่าน)
> และ `OPTIONAL_THRESHOLDS` ซึ่งตอนนี้มี `caregiver_location_max_age_seconds` ตัวเดียว
> การแบ่งนี้ **เกิดจากความผิดพลาดจริงเมื่อ 27 ส.ค.** ดูหัวข้อ 5.1

อธิบาย workflow ของ Module 3 หลัง refactor: ย้ายกฎ/เกณฑ์ทั้งหมดออกจากโค้ดไปเก็บใน
ตาราง Knowledge Base (KB) บน Database, ดึงมาใช้ตอน runtime, แก้ได้โดยไม่ต้องแตะโค้ด
(No Hardcode / Dynamic) พร้อมอ้างอิงแหล่งการแพทย์จริง — ตรงตามที่อาจารย์กำหนด
(ชื่อไฟล์/ฟังก์ชัน/ตารางเป็นภาษาอังกฤษ คำอธิบายเป็นภาษาไทย)

> อัปเดตล่าสุด: 2026-07-09 — เอกสารนี้แทนที่คำอธิบาย Module 3 เวอร์ชันเก่าใน
> `data_flow.md` / `database_layer.md` (ซึ่งเขียนก่อน refactor ตอนกฎยัง hardcode อยู่)

---

## 1. โครงสร้างไฟล์ — ไฟล์ไหนอยู่โฟลเดอร์ไหน ทำอะไร

แบ่งเป็น 3 ชั้นตามหน้าที่ (แยก "สมองคำนวณ" ออกจาก "การคุย DB" อย่างชัดเจน)

### ชั้น A — Pure Logic 🟡🔵 (คำนวณล้วน ห้าม import DB)
📁 `app/ai/module3_risk/`

| ไฟล์ | หน้าที่ |
|---|---|
| `data_normalization.py` | แปลงค่าดิบ → 0–1 (เช่น ระยะเมตร ÷ `route_deviation_ceiling_m`) |
| `risk_score_calculation.py` | ถ่วงน้ำหนัก 5 ปัจจัย → คะแนน 0–100 + ระดับ (รับ `weights`, `ceilings` เป็นพารามิเตอร์) |
| `emergency_decision_engine.py` | ตัดสินฉุกเฉิน: `danger_zone` OR `score > emergency_score` (รับเกณฑ์เข้ามา) |
| `gps_failure_handling.py` | ตรวจ GPS หาย: `gap > threshold_s` (รับ `threshold_s` เข้ามา) |
| `risk_data_collection.py` | รวมปัจจัยดิบ 5 ตัวจาก GPS — เรียก detector ของ Module 2 (รับ `danger_zones` เข้ามา) |
| `temporal_adjustment.py` | กฎประวัติ: trend (+boost) / sustained (บังคับฉุกเฉิน) — รับ `temporal_rules` เข้ามา |
| `__init__.py` | export ฟังก์ชันทั้งหมดของ Module 3 |

> **หลักการ:** ชั้นนี้ **ไม่มี** `import` จาก `app.db` เลย — รับค่ากฎเป็นพารามิเตอร์
> ทำให้ (1) unit-test ได้โดยไม่ต้องมี DB (2) กรรมการ grep แล้วไม่เจอเลขกฎในโค้ด

### ชั้น B — API / Orchestrator ⚪ (สั่งงาน + คุย DB)
📁 `app/api/`

| ไฟล์ | หน้าที่ |
|---|---|
| `risk.py` | **หัวใจ** — `GET /api/risk/{patient_id}` : ดึงกฎ+ข้อมูล → เรียกชั้น A → บันทึกผล |
| `search_area.py` | Module 4 — ยืม `detect_gps_gap` และอ่าน `gps_gap_seconds` จาก KB เดียวกัน (แก้ค่าเดียว มีผลทั้ง 2 endpoint) |
| `admin_rules.py` | `GET /api/admin/rules` + `/api/admin/rules/history` — ให้กรรมการเปิดดูกฎ + แหล่งอ้างอิง + ประวัติการแก้ |

### ชั้น C — Storage / Data ⚪ (ตู้เก็บกฎ + ประวัติ)
📁 `app/db/`

| ไฟล์ | หน้าที่ |
|---|---|
| `models.py` | นิยาม 5 ตาราง KB (+ ตารางประวัติเดิม) |
| `rule_repository.py` | อ่าน/แก้กฎใน KB: `get_active_weights/thresholds/danger_zones/temporal_rules`, `update_*` (versioning + audit) |
| `crud.py` | `get_gps_history`, `get_recent_risk_scores`, `save_risk_score`, `save_alert` |

📁 `app/mock/`
| ไฟล์ | หน้าที่ |
|---|---|
| `seed_risk_rules.py` | ใส่ค่ากฎเริ่มต้นทั้งหมด + แหล่งอ้างอิงการแพทย์ลง DB (idempotent) |

---

## 2. Workflow — การไหลของข้อมูล 1 รอบ (`GET /api/risk/{id}`)

```
[ผู้ใช้เรียก GET /api/risk/10]
        │
        ▼
 risk.py (Orchestrator)
 ├─ 0.  ดึงกฎล่าสุดจาก KB   ── rule_repository ──► DB   (weights, thresholds,
 │                                                       danger_zones, temporal_rules)
 ├─ 1.  ดึง profile + GPS 30 วัน  ── crud ──► DB
 │      + ประวัติคะแนน 5 รอบล่าสุด (get_recent_risk_scores)
 ├─ 3.  collect_risk_factors() ───────────► เรียก detector Module 2
 │      → ปัจจัยดิบ 5 ตัว                     (wandering / route / confusion)
 ├─ 4.  normalize → ค่า 0–1
 ├─ 5.  calculate_risk(factors, weights) → คะแนน "ฐาน"
 ├─ 5b. apply_temporal_rules(ประวัติ) → คะแนน "สุทธิ" (trend +10 / sustained)
 ├─ 6.  detect_gps_gap() → GPS หายไหม
 ├─ 9.  decide_emergency(คะแนนสุทธิ) + sustained override → ฉุกเฉินไหม
 ├─ 8.  save_risk_score() ── crud ──► DB (risk_scores)   ← ประวัติรอบใหม่
 └─ 9/10. save_alert() ── crud ──► DB (alerts)           ← ถ้าฉุกเฉิน/GPS หาย
        │
        ▼
[ตอบ JSON: risk_score, base_risk_score, temporal_adjustment,
            temporal_rules_triggered, risk_level, emergency, reason]
```

**เป็น Loop:** ดึงกฎ + ประวัติ → คำนวณสด → ปรับด้วยประวัติ → บันทึกเป็นประวัติของรอบถัดไป

---

## 3. เก็บยังไง — ตารางใน DB (Neon PostgreSQL)

### กลุ่มกฎ (Knowledge Base) — แก้ได้โดยไม่แตะโค้ด
| ตาราง | model | เก็บอะไร |
|---|---|---|
| `risk_factor_weights` | `RiskFactorWeight` | น้ำหนัก 5 ปัจจัย (รวม = 1.0) |
| `risk_thresholds` | `RiskThreshold` | เกณฑ์คะแนน/ระยะ/เวลา + `unit` |
| `danger_zones` | `DangerZone` | วงกลมเขตอันตราย (lat/lng/radius/type) |
| `temporal_rules` | `TemporalRule` | กฎประวัติ — พารามิเตอร์เป็น JSON |
| `rule_audit_log` | `RuleAuditLog` | log ทุกการแก้กฎ (table/field/old→new/ใคร/เมื่อ/เหตุผล) |

ทุกตารางกฎมีคอลัมน์ร่วม: `active`, `version`, `source_reference`, `rationale`, `effective_from`, `created_by`

### กลุ่มข้อเท็จจริง (Facts)
| ตาราง | เก็บอะไร |
|---|---|
| `risk_scores` | คะแนน "สุทธิ" ทุกรอบ = ประวัติสำหรับกฎ temporal |
| `alerts` | การแจ้งเตือนที่เกิดขึ้นจริง |

### กลไก Versioning + Audit (ตอบกรรมการเรื่อง "แก้กฎแล้วตามรอยได้ไหม")
แก้กฎ = **ไม่ทับ** ของเก่า แต่ทำใน transaction เดียว:
1. แถวเก่า → `active = false`
2. เพิ่มแถวใหม่ → `active = true`, `version + 1`
3. เขียน `rule_audit_log` 1 แถว

ผล: มีแถว `active` เพียง **1 แถวต่อชื่อ** เสมอ, เก็บประวัติครบ, log กับสถานะกฎไม่มีวันขัดแย้งกัน
ทุก query อ่านกฎกรองด้วย `WHERE active = true` — แถวเวอร์ชันเก่าจึงไม่กระทบการทำงาน

---

## 4. ใช้อะไร เอามาจากไหน (Dependencies)

| ต้องการ | เอามาจาก |
|---|---|
| ปัจจัย wandering / route / confusion | **Module 2** (`module2_prediction` detectors) — Module 3 เรียกใช้ ไม่ได้เขียนเอง |
| `known_places` (สถานที่คุ้นเคย) | **Module 1** (ตาราง `behavioral_profiles`) |
| GPS history | ตาราง `gps_data` (ผ่าน `crud`) |
| กฎ + เกณฑ์ + เขตอันตราย + กฎ temporal | ตาราง KB (ผ่าน `rule_repository`) |
| ประวัติคะแนนเก่า | ตาราง `risk_scores` (ผ่าน `crud.get_recent_risk_scores`) |

> Module 3 คือ "ผู้ประกอบร่าง": รับปัจจัยจาก M1/M2 มาให้คะแนนตามกฎใน DB

---

## 5. เกณฑ์ทางการแพทย์ — ค่าไหนอ้างอิงอะไร (ค่าที่ seed จริง)

### น้ำหนักปัจจัย (`risk_factor_weights`)
| ปัจจัย | น้ำหนัก | แหล่งอ้างอิง |
|---|---|---|
| route_deviation | 0.30 | TH-DMS-2564 §BPSD + TH-RAMA-BPSD (หลงทิศเป็นอาการหลัก) |
| wandering | 0.25 | TH-SIRIRAJ nursing manual 2562 (พบ >90% ใน BPSD) |
| confusion | 0.20 | TH-DMS-2564 §BPSD (เกณฑ์วินิจฉัย Mild Dementia) |
| danger_zone | 0.15 | Alzheimer's Assoc Safe Return (ใกล้ถนน/น้ำ เสี่ยง +60%) |
| unfamiliarity | 0.10 | TH-DMS-2564 (ปัจจัยบริบท) |

### เกณฑ์ (`risk_thresholds`)
| เกณฑ์ | ค่า | หน่วย | แหล่งอ้างอิง |
|---|---|---|---|
| low_ceiling | 50 | score | MOPH ED Triage 2561 + ESI → คะแนน <50 = Level 4-5 (เฝ้าดู) |
| medium_ceiling | 80 | score | MOPH ED Triage 2561 → ≥80 = Level 1-2 (แทรกแซง 0-10 นาที) |
| emergency_score | 80 | score | MOPH Level 1-2 + Alzheimer's (50% เสียชีวิต/บาดเจ็บถ้าไม่พบใน 24 ชม.) |
| route_deviation_ceiling_m | 500 | meter | Alzheimer's (94% พบใน ~2.4 กม.; 500 ม. เป็นขอบเตือนต้น) |
| push_cooldown_seconds | 600 | second | เว้นระยะขั้นต่ำระหว่าง push ชนิดเดียวกัน (เพิ่ม 22 ส.ค.) — MOPH Level 1-2 แทรกแซง 0–10 นาที |
| gps_gap_seconds | 600 | second | Ali et al. (10 นาที = ช่วงวิกฤตก่อนเสี่ยงบาดเจ็บ) |
| sos_cooldown_seconds | 60 | second | MOPH ED Triage 2561 Level 1-2 + BPSD พฤติกรรมทำซ้ำ (เพิ่ม 26 ส.ค. พร้อมปุ่ม SOS) — **แยกจาก `push_cooldown_seconds` เพราะคุมคนละเรื่อง**: การเตือนอัตโนมัติซ้ำเพราะเงื่อนไขยังอยู่ 10 นาทีจึงเหมาะ ส่วน SOS ซ้ำเพราะ*คนกดอีกครั้ง* การกดครั้งที่สองคือข้อมูลใหม่ ไม่ใช่เสียงรบกวน |

เจ็ดตัวข้างบนคือ `KNOWN_THRESHOLDS` (`rule_repository.py:41-44`)

### 5.1 เกณฑ์ที่ "ขาดได้" — `OPTIONAL_THRESHOLDS`

| เกณฑ์ | ค่า | หน่วย | ใครอ่าน | แหล่งอ้างอิง |
|---|---|---|---|---|
| caregiver_location_max_age_seconds | 1800 | second | `api/users.py:267` (จัดอันดับผู้ดูแลตามระยะทาง) | ค่าตั้งต้นสำหรับการทดลอง ยังรอช่วงเวลารายงานตำแหน่งจริงจากฝั่งแอป |

**ทำไมต้องแยกสองชั้น — นี่ไม่ใช่การจัดระเบียบ แต่มาจากของที่พังจริง**

`get_all_thresholds` **ปฏิเสธที่จะโหลดถ้าเกณฑ์ใน `KNOWN_THRESHOLDS` ขาดไปแม้ตัวเดียว** ความเข้มงวด
นั้นถูกสำหรับตัวเลขที่ใช้*คิดคะแนน* เพราะชุดกฎที่โหลดมาครึ่งเดียวจะให้คะแนนที่ดูปกติแต่ผิด —
แต่มัน**ผิดสำหรับลูกบิดที่แค่จัดลำดับรายการ** ถ้าเอา `caregiver_location_max_age_seconds`
ไปใส่ใน `KNOWN_THRESHOLDS` การ deploy โค้ดนี้ลงฐานข้อมูลที่ยังไม่ได้ seed ใหม่จะทำให้
**การคิดคะแนนเสี่ยงล่มทั้งระบบ** เพื่อฟีเจอร์ที่การคิดคะแนนไม่ได้ใช้เลย

**เรื่องนี้เกิดขึ้นแล้วจริงเมื่อ 27 ส.ค.** ตอนที่ `sos_cooldown_seconds` หายไปหนึ่งแถว แล้วสิ่งที่หยุด
ทำงานคือ *risk scoring* ไม่ใช่ SOS

🪤 **ผลที่ตามมาซึ่งมองไม่เห็นจากตารางข้างบน:** Neon จึงมีเกณฑ์ **8 แถว active** ขณะที่
`KNOWN_THRESHOLDS` มี 7 — โค้ดรุ่นเก่าเทียบสองชุดนี้ด้วย `!=` ซึ่งแปลว่าสภาพปัจจุบันนี้เอง
จะทำให้โหลดไม่ผ่าน แก้แล้วและมีเทสต์คุม แต่ **อย่าเขียนโค้ดที่ยืนยันว่าจำนวนแถวต้องเท่ากับ 7**

### เขตอันตราย (`danger_zones`)
| เขต | รัศมี | type | แหล่งอ้างอิง |
|---|---|---|---|
| Major highway interchange (demo) | 150 m | highway | Alzheimer's Assoc Safe Return Guide |
| Canal / waterway edge (demo) | 200 m | waterway | Alzheimer's Assoc Safe Return Guide |

### กฎประวัติ (`temporal_rules`)
| กฎ | พารามิเตอร์ | แหล่งอ้างอิง |
|---|---|---|
| trend_escalation | `{window: 3, boost: 10}` | NEWS (NHS National Early Warning Score) — แนวโน้มขึ้น 3 รอบ = เตือนก่อนวิกฤต |
| sustained_high_risk | `{window: 5, min_score: 50}` | MOPH ED Triage Level 3 (Urgent 30 นาที) — เสี่ยงต่อเนื่อง 5 รอบ → ยกเป็นฉุกเฉิน |

> **MOPH ED Triage 2561** = แนวทาง triage ระดับชาติของไทย (กรมการแพทย์ กระทรวงสาธารณสุข พิมพ์ครั้งที่ 2, 2561;
> ~75.8% ของโรงพยาบาลไทยใช้, ดัดแปลงจาก ESI v4) — map คะแนน 50/80 เข้ากับ Level การ triage อย่างเป็นทางการ

---

## 6. สูตรและระดับ (สรุป)

**คะแนนฐาน** = Σ (weight × factor) × 100 (น้ำหนักรวม = 1.0 → คะแนน 0–100 เสมอ)

**ระดับ:** `<50` = low · `50–79` = medium · `≥80` = high

**ฉุกเฉิน** (`>` แบบเข้ม): `danger_zone` OR `score > 80` OR `sustained_high_risk`
(หมายเหตุ: ระดับ high ใช้ `≥80` แต่ฉุกเฉินใช้ `>80` — คนละตัวเปรียบเทียบ แม้เป็นเลข 80 เหมือนกัน)

**Temporal:** trend บวก +10 ก่อนจัดระดับ · sustained บังคับ `emergency=true` reason `sustained_risk`
(ถ้าประวัติไม่พอ กฎไม่ทำงาน → ผู้ป่วยใหม่ได้คะแนนเท่าเดิมเป๊ะ = cold-start parity)

---

## 7. ทดสอบและพิสูจน์

- **pytest 346 tests ผ่าน 0 xfailed** (ณ 27 ส.ค. เดิม 226 + 1 xfailed ณ 22 ส.ค.) — ครอบคลุม pure logic, KB repository, admin endpoints, temporal, e2e, โซ่แจ้งเตือน, FCM, auth, จับคู่เครื่อง, SOS, ขออนุมัติเดินทาง
- **Dynamic ไม่ต้อง restart:** แก้ค่าใน DB (เช่น `emergency_score` 80→50) แล้ว request ถัดไปเห็นทันที (ไม่มี cache)
- **Behavior parity:** พิสูจน์แล้วว่าคะแนนก่อน/หลัง refactor เท่ากันเป๊ะ (24.5 ปกติ / 72.5 เขตอันตราย)
