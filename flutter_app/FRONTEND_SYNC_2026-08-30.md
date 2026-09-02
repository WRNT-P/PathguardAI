# ถึงฝั่ง backend — 30 ส.ค. 2026 (สรุปงาน frontend วันนี้ + progress เทียบ report)

Branch `frontend1` (แยกจาก `main` วันที่ 29 ส.ค.) — 6 commits วันนี้ ยังไม่เปิด PR รอ review ก่อน

## สรุป 3 บรรทัด

1. 🟢 **Trip approval flow เสร็จและทดสอบผ่านจริงแล้ว** — ผู้ป่วย (ทั้ง Level 1 และ Level 2) ขออนุมัติไปสถานที่ผ่าน Firebase Realtime DB แบบ real-time ผู้ดูแลเห็นคำขอ อนุมัติ/ปฏิเสธได้ ฝั่งผู้ป่วยเห็นผลทันที — ทดสอบข้าม process จริงแล้ว (ยังไม่ได้ทดสอบข้าม 2 device จริง เดี๋ยวอธิบายด้านล่าง)
2. 🟡 **`GET /api/recommendation/{id}` ยังไม่ต่อ** — รู้แล้วว่าต่อได้เลยวันนี้ (จากเอกสาร `APP_SYNC_2026-08-29_2.md`) แต่ยังไม่ได้ลงมือ เพราะเซสชันวันนี้โฟกัส Firebase กับ trip approval ก่อน จะทำเป็นอันดับถัดไป
3. 🔴 **มีคำถามกลับไปหาพวกคุณ 1 ข้อ** — เรื่อง `claimed_by_latitude/longitude` ที่ถามไว้ใน `APP_SYNC_2026-08-29_2.md` ยังไม่ได้ตอบ ดูหัวข้อท้ายเอกสารนี้

---

## 1. Commit-by-commit (เรียงจากเก่าไปใหม่)

### `3b11c0d` — Add Firebase (Realtime DB) to the Flutter app

**ทำอะไร:** ติดตั้ง Firebase เข้าแอป Flutter เต็มรูปแบบ — `firebase_core` + `firebase_database` (ใช้ Realtime DB ตรงกับสถาปัตยกรรมใน `CLAUDE.md` ที่ระบุว่า live/ephemeral data ไปที่ Realtime DB ไม่ใช่ Firestore) รัน `flutterfire configure` สร้าง `lib/firebase_options.dart`, `android/app/google-services.json`, `ios/Runner/GoogleService-Info.plist` แล้วเรียก `Firebase.initializeApp()` ใน `main.dart` ก่อน `runApp()`

**ทำไมต้องทำ:** เป็น prerequisite ของ trip approval flow (ข้อถัดไป) — ต้องมีช่องทาง sync ข้อมูลแบบ real-time ระหว่างแอปผู้ป่วยกับแอปผู้ดูแล (สอง device แยกกัน) ซึ่งใน memory ของทีมมีบันทึกไว้ว่าเพื่อน backend เพิ่มเราเป็น Editor บน Firebase project `pathguard-ai-2d047` ไว้ตั้งแต่ 29 ส.ค. แล้ว

**ปัญหาที่เจอระหว่างทำ (บันทึกไว้เผื่อคนอื่นเจอ):** รัน `flutterfire configure` ก่อนสร้าง Realtime Database instance จริงในหน้า Console — ผลคือไฟล์ config ที่ generate มาไม่มี `databaseURL` เลย ทำให้ทุก write ไป Firebase เงียบหาย ไม่มี error อะไรขึ้นเลย เหมือนโค้ดไม่ทำงาน วิธีแก้คือสร้าง Realtime Database ใน Console ก่อน แล้ว rerun `flutterfire configure` ใหม่ ถึงจะ pick up URL มาใส่ให้ถูก

**Path ที่ใช้ตอนนี้:** `trip_requests/{pushId}` — เป็น path แบบเปิดกว้าง ไม่ scope ตาม patient/caregiver ID เพราะแอปยังไม่มีระบบ pairing/login จริง (ตรงกับที่คุณบอกไว้ว่า pairing endpoint ยังรอ frontend อยู่) เดี๋ยวพอมี pairing จริงจะกลับมา scope path นี้ทีหลัง

---

### `1efd692` — Add Google Places Autocomplete search on Patient Level 1

**ทำอะไร:** เพิ่มระบบค้นหาสถานที่จริง (ไม่ใช่ list mock) ในหน้า Patient Level 1 ด้วย Google Places API — พิมพ์ค้นหา มี debounce 450ms กันยิง API ทุกตัวอักษร ใช้ session token ตาม Google's billing model (autocomplete + place details นับเป็น session เดียว ถูกกว่าแยกนับทีละ request) ผลลัพธ์เป็นสถานที่จริงในโลก ไม่ใช่ 3 ที่ hardcode เหมือนเดิม

**ทำไมต้องทำ:** ผู้ใช้ (Person 1) ต้องการให้ Patient Level 1 ค้นหาสถานที่จริงได้ ไม่ใช่แค่ filter list ปลอมๆ

**เรื่องที่เกี่ยวกับ billing ที่ตั้งใจระวังเป็นพิเศษ:** ใช้ **Places API (New)** ไม่ใช่ legacy Places API (โปรเจกต์ปิด legacy ไว้ ต้องเปิด "Places API (New)" แยกใน Cloud Console ถึงจะยิงผ่าน) มี debounce + session token ครบตามที่ควรจะเป็น เพื่อไม่ให้ burn free credit เร็วเกินไป

---

### `c1d3791` — Make trip requests sync live between patient and caregiver via Firebase

**ทำอะไร:** เปลี่ยน `TripRequestDirectory` จาก in-memory `ChangeNotifier` (ใช้ได้แค่ใน process เดียว) เป็น Firebase-backed จริง — เขียนคำขอไป `trip_requests/{id}`, ฝั่งผู้ดูแลมี listener (`onValue`) เห็นคำขอใหม่ real-time, กด Approve/Reject เขียน status กลับ, ฝั่งผู้ป่วยที่กำลังรออยู่ (`await request.decision`) ก็ resolve ทันทีที่เห็น status เปลี่ยนจาก Firebase — ข้ามคนละ device ได้จริง (ไม่ใช่แค่ในเครื่องเดียวกัน)

**ทำไมต้องทำ:** ก่อนหน้านี้ระบบ trip approval (มีอยู่แล้วฝั่ง Level 2) ทำงานได้แค่ตอน patient/caregiver อยู่ใน process/เครื่องเดียวกัน ใช้จริงกับ 2 เครื่องแยกกันไม่ได้เลย

**สิ่งที่ทดสอบผ่านแล้ว:** เปิดแอป, ขอไปสถานที่จาก Level 2, เช็คใน Firebase Console เห็น node เกิดขึ้นจริง (`status: pending`), กด Approve จากหน้า caregiver → หน้า patient เข้าสู่ navigation screen ทันที ทดสอบทั้ง 2 ทิศทาง (approve และ reject) ผ่าน

**ที่ยังไม่ได้ทดสอบ:** รันบน 2 emulator/device จริงพร้อมกัน — ที่ผ่านมาทดสอบด้วยการสลับ login role บนแอป instance เดียวกัน (ใช้ได้เพราะข้อมูลจริงๆ ไปเก็บที่ Firebase ไม่ใช่ในเครื่อง แต่ยังไม่เห็น live-push ระหว่าง 2 หน้าจอพร้อมกันด้วยตาตัวเอง)

**Scope decision ที่ทำไปด้วย:** เดิม flow "ขออนุมัติก่อนไป" มีแค่ Level 2 (Level 1 กด Start แล้วเข้า navigation ทันที) วันนี้เปลี่ยนให้ **ทั้ง Level 1 และ Level 2 ต้องขออนุมัติก่อนไปทุกที่** ตามการตัดสินใจของผู้ใช้ (Person 1) — ไม่ใช่ requirement จาก report โดยตรง แต่เป็น UX decision ของทีม frontend

---

### `8150b8b` — Wire the caregiver Call button to actually dial

**ทำอะไร:** ปุ่ม "Phone/Call" ในการ์ดผู้ดูแล (หน้า SOS contact ทั้ง Level 1 `CaregiverCard` และ Level 2 `CaregiverTile`) เดิม `onPressed` ว่างเปล่า วันนี้ต่อให้เปิดแอปโทรศัพท์จริงผ่าน `url_launcher` (`tel:` scheme) เบอร์โทรตอนนี้ยัง mock อยู่ในโค้ดตรงๆ (ไม่ได้ดึงจาก database จริง)

**ทำไมต้องทำ:** เป็นฟีเจอร์ที่ขาดหายไปเดิม (ปุ่มกดไม่มีผล) พบระหว่างเช็คหน้า SOS contact screen

**ยังไม่ทำ:** ดึงเบอร์โทรจริงจาก backend — ตอนนี้รอ `GET /api/recommendation` เสร็จก่อน แล้วน่าจะไล่ทำ endpoint อื่นๆ ที่เกี่ยวกับ caregiver contact info ต่อ (ยังไม่ได้คุยกับทีมว่ามี endpoint นี้อยู่แล้วหรือยัง)

---

### `20a7b84` — Remove Android stretch overscroll effect on the login form

**ทำอะไร:** แก้บั๊ก UI เล็กๆ — หน้า `form_login_screen.dart` (caregiver) เลื่อนหน้าจอเกินขอบแล้วภาพ stretch ผิดปกติบน Android แก้ด้วย `ScrollConfiguration(behavior: ...copyWith(overscroll: false))` ครอบ `SingleChildScrollView` ที่มี `physics: ClampingScrollPhysics()`

**ทำไมต้องทำ:** bug report จากผู้ใช้เอง ไม่เกี่ยวกับ backend เลย ใส่ไว้ในสรุปเพื่อความครบถ้วน

---

### `34d18f2` — Add remaining Flutter platform files and services

**ทำอะไร:** commit ไฟล์ที่มีอยู่ในเครื่องมาสักพักแล้วแต่ไม่เคย `git add` เลย (เป็น "งานค้าง" ไม่ใช่งานใหม่วันนี้) — ไฟล์ platform boilerplate ทั้งหมด (`android/`, `ios/`, `linux/`, `macos/`, `web/`, `windows/` ที่ `flutter create` generate ให้), และหน้าจอ/service ที่ build เสร็จไปแล้วก่อนหน้านี้แต่ไม่เคย commit: `add_patient_screen.dart`, `caregiver_homepage_screen.dart`, `caregiver_login_screen.dart`, `track_screen.dart`, `login_screen.dart`, `navigation_screen.dart` (ทั้ง Level 1/2), `patient_login_screen.dart`, `api_client.dart`, `directions_service.dart`, `location_service.dart`, `patient_directory.dart`, `sos_service.dart`, `utils/bearing.dart`

**ทำไมต้องทำ:** เพิ่งมาสังเกตว่า repo ทั้ง `flutter_app/` เกือบทั้งโฟลเดอร์ยัง untracked อยู่บน `main` (น่าจะเพราะไม่เคย push ครั้งแรกตั้งแต่สร้างโปรเจกต์) รวมเป็น commit เดียวเพราะเป็น "เก็บงานเก่าที่ค้าง" ไม่ใช่ feature ใหม่ที่ควรแยกอธิบาย

---

## 2. Progress เทียบ spec ในรายงาน "PathGuard AI (2).pdf"

### Patient module (spec: 7 features)

| Feature | สถานะ | หมายเหตุ |
|---|---|---|
| Smart Recommendation | 🟡 Mock | List สถานที่ยัง hardcode อยู่ทั้ง Level 1/2 ยังไม่ต่อ `GET /api/recommendation/{id}` |
| Real-time Navigation | 🟢 บางส่วน | Directions API จริงใช้งานได้ (`navigation_screen.dart` ทั้ง 2 level) แต่เป็นเส้นทาง Google คำนวณให้ ไม่ใช่ auto-reroute แบบ real-time |
| Off-route Detection | ⚪ Out of scope | ตกลงกันไว้ก่อนหน้าว่าไม่ทำใน MVP นี้ |
| Zero-UI Mode | ⚪ Out of scope | เหมือนข้างบน |
| Safe Zone Navigation (พาไปสถานีตำรวจ/รพ./วัดที่ใกล้ที่สุด) | ⚪ ยังทำไม่ได้ | รอ backend มี data model รองรับ (ไม่มี safe-place-type table ตอนนี้) ไม่ใช่งานที่ frontend ทำเองได้ |
| SOS Emergency | 🟡 Partial | ปุ่มกดได้ มี UI feedback แต่ `sos_service.dart` ยังเป็น stub ไม่ได้ยิง alert จริงไป backend |
| Push Notification | 🔴 ยังไม่ทำ | ยังไม่ได้เริ่ม |

### Caregiver module (spec: 6 features)

| Feature | สถานะ | หมายเหตุ |
|---|---|---|
| Trip Approval | 🟢 เสร็จวันนี้ | Real-time ผ่าน Firebase ทั้ง 2 level ทดสอบผ่านแล้ว (ดูรายละเอียด commit `c1d3791` ด้านบน) |
| Caregiver Community chat | 🔴 ยังไม่ต่อ | 
| Realtime Tracking | 🟡 Mock | `TrackScreen` มี map + marker แล้ว แต่ตำแหน่ง/risk score ยังเป็น `Future.delayed` mock ไม่ใช่ของจริงจาก backend |
| Realtime Notification | 🟡 Partial | `NotificationScreen` ตอนนี้โชว์แค่ trip request เท่านั้น (ตัดสินใจไว้เมื่อวานว่าจะโชว์แค่นี้ก่อน) ยังไม่รวม alert ประเภทอื่น |
| SOS Notification | 🔴 ยังไม่ต่อ | รอ SOS Emergency ฝั่ง patient เสร็จก่อน |
| Distance Ranking | 🔴 Frontend ยังไม่ใช้ | รู้ว่า backend มี `RankedCaregiver` แล้ว (จาก `APP_SYNC_2026-08-29_2.md`) แต่ frontend ยังไม่มีหน้าจอไหนเรียกใช้เลย |

---

## 3. สิ่งที่จะทำต่อ (ฝั่ง frontend เรียงตาม priority)

1. ต่อ `GET /api/recommendation/{id}` จริง แทน `recommendedPlaces` mock — รู้แล้วว่าทำได้เลยวันนี้ไม่ต้องรอ Firebase
2. ทดสอบ trip approval flow บน 2 emulator/device จริงพร้อมกัน (ตอนนี้ทดสอบด้วยการสลับ role เครื่องเดียว)
3. ~~Firebase Auth / pairing จริง~~ — **เริ่มแล้ว 30 ส.ค. ดูหัวข้อ 5 ด้านล่าง** ฝั่งผู้ป่วยเสร็จ ฝั่งผู้ดูแลยังไม่เริ่ม
4. ต่อ SOS ให้ยิง alert จริงไป backend แทน stub ปัจจุบัน
5. ดึงเบอร์โทรผู้ดูแลจริงมาแทน mock (ต่อจากข้อ 1 คงง่ายขึ้นถ้ามี endpoint คล้ายกันอยู่แล้ว)

---

## 4. อัพเดต 30 ส.ค. — เริ่มต่อ pairing จริงแล้ว (§2 ในสัญญา)

**เอา `PatientDirectory` mock ออก เปลี่ยนเป็นยิง backend จริงตามสัญญา §2 ทีละขั้น:**

- `firebase_auth` ติดตั้งแล้ว, `api_client.dart`'s `_getAuthToken()` อ่านจาก `FirebaseAuth.instance.currentUser?.getIdToken()` จริงแล้ว (ไม่ใช่ `null` เฉยๆ อีกต่อไป)
- **ฝั่งผู้ดูแล** (`caregiver_homepage_screen.dart`): "Add Patient" ยิง `POST /api/patients` จริง โชว์ `pairing_code` จริงที่ backend ส่งมา (ไม่ใช่ id ปลอมที่ generate เอง)
- **ฝั่งผู้ป่วย** (`patient_login_screen.dart`): กรอกรหัสยิง `POST /api/pair` จริง แล้ว `signInWithCustomToken` ทันที (เป็นจุดที่ Firebase user ตัวจริงถูกสร้างขึ้น) อ่าน `severity_level` จาก response มาตัดสิน routing Level 1/2 (ไม่ default เป็น 1 ตามที่สัญญาเตือนไว้ — ถ้า `null` โชว์ error ให้ไปขอผู้ดูแลตั้งค่าก่อน) แล้วยิง `GET /api/patients/{id}` เพิ่มอีกรอบเพื่อเอาชื่อผู้ป่วย (response ของ `/api/pair` เองไม่มี `name`)

🟡 **ที่ยังไม่จบ (ต่อพรุ่งนี้):** `POST /api/patients` ตอนนี้ยังส่ง **`caregiver_id: 12` แบบ hardcode** เพราะฝั่งผู้ดูแล (`caregiver_login_screen.dart`, `form_login_screen.dart`) **ยังไม่ต่อ Firebase Auth/`.` `POST /api/register` เลยแม้แต่นิดเดียว** — เข้าใจว่าต้อง sign in Firebase ปกติ (ไม่ใช่ custom token แบบผู้ป่วย) แล้วค่อย `POST /api/register` ด้วย `role: "caregiver"` ตามที่ระบุไว้ใน "ข้อควรรู้" ท้ายหัวข้อ §2 — จะเริ่มทำต่อพรุ่งนี้

🟡 **ยังไม่ได้เช็คใน Firebase Console ว่ามี user จริงเกิดขึ้นหลัง pairing** — คุยกันไว้ว่าน่าจะใช่ตามทฤษฎี (backend pre-assign uid ตอน `POST /api/patients`, Firebase สร้าง record จริงตอน `signInWithCustomToken`) แต่ยังไม่ได้ verify ด้วยตาจริง

---

## 5. คำถามกลับไปหาพวกคุณ

จาก `APP_SYNC_2026-08-29_2.md` ที่ถามว่าจะเอา `claimed_by_latitude` / `claimed_by_longitude` / `claimed_by_location_age_s` เพิ่มใน `AlertOut` ไหม — **[ยังไม่ได้ตัดสินใจ จะตอบแยกอีกที]** ไม่ block งานที่ทำอยู่ตอนนี้
