# ถึงฝั่งแอป — ชุดตกแต่ง UI สำหรับ `frontend1`

**2026-08-30 · จากฝั่ง backend**

---

> ⚠️ **ไม่มีอะไรในเอกสารนี้เคยถูก compile**
>
> เครื่องที่เขียนเอกสารนี้ **ไม่มี Flutter** (`flutter: command not found`) โค้ด Dart
> ทุกบรรทัดข้างล่างเป็น *ร่าง* ยังไม่เคย `flutter analyze` ไม่เคยรัน ไม่เคยเห็นบนจอจริง
> อย่า copy ทั้งก้อนแล้ว push — อ่าน ตัดสินใจเอง แล้วเขียนใหม่ในแบบที่ compile ผ่าน
>
> เหตุผลเดียวกับที่เอกสารก่อนหน้าทุกฉบับมีแบนเนอร์นี้ โครงสร้างจอเป็นของคุณ เราไม่แตะ

---

## 1. นี่คืออะไร

`frontend1` มีจอครบแล้วและ **โครงสร้างแน่น** — state machine `browsing / waitingApproval /
rejected`, ตัวหนังสือใหญ่ฝั่งผู้ป่วย, ปุ่ม SOS แยกออกมาชัดเจน ทั้งหมดนี้ถูกแล้ว และไม่ถูกแตะ

สิ่งที่ยังไม่มีคือ **การตกแต่ง**:

* `main.dart` ส่ง `MaterialApp(home: ...)` โดย **ไม่มี `theme:` เลย** ทุกจอจึงได้ Material 3
  default — นั่นคือที่มาของ AppBar สีม่วง
* ปุ่มเป็น `Colors.grey[400]` ตัวหนังสือดำ (`login_screen.dart`), การ์ดเป็น `Colors.grey[200]`
* ไม่มี color token ไม่มี type scale ไม่มีระบบระยะห่าง — ทุกค่าเขียนสดตรงจุดที่ใช้

เอกสารนี้คือชุดโทเคน + ธีม ที่ทำให้ทุกจอดูเป็นชุดเดียวกัน **โดยไม่แตะ logic แม้แต่บรรทัดเดียว**

**ดูภาพก่อนอ่าน** — prototype อยู่ที่: `<<< วางลิงก์ Claude Design ตรงนี้ >>>`

---

## 2. สี — เก็บความหมายเดิม เปลี่ยนแค่เฉด

ความหมายของสีในโค้ดปัจจุบันถูกต้องอยู่แล้ว และถูกเก็บไว้ทั้งหมด:
น้ำเงิน = ปุ่มกด · แดง = ฉุกเฉิน · เขียว = ปลอดภัย/ว่าง/อนุมัติ · ส้ม = ระวัง

**ที่เปลี่ยนคือค่าความสว่าง เพราะของเดิมตกเกณฑ์ contrast:**

| ใช้ทำอะไร | เดิม | ใหม่ | contrast กับตัวอักษรขาว |
|---|---|---|---|
| ปุ่มหลัก | `Colors.blue` `#2196F3` | `#1D63C9` | 3.1:1 ❌ → **5.9:1 ✅** |
| ฉุกเฉิน / SOS | `Colors.red` `#F44336` | `#D92D20` | 3.7:1 ❌ → **4.8:1 ✅** |
| อนุมัติ / ว่าง | `Colors.green` `#4CAF50` | `#0E7A4F` | 2.8:1 ❌ → **4.9:1 ✅** |
| ระวัง / กำลังเดินทาง | `Colors.orange` | `#B54708` | ✅ |
| แถบบอกทาง | `Color.fromARGB(255, 50, 95, 68)` | **`#325F44` — ค่าเดิมเป๊ะ** | ✅ |

สามสีแรกในรูปแบบเดิม ใส่ตัวหนังสือขาวแล้ว **ตกเกณฑ์ WCAG AA** ซึ่งกับผู้ใช้อายุ 65+
ไม่ใช่เรื่องที่เก็บไว้ทำทีหลังได้

**เฉดรอง** (พื้นอ่อนของ pill, ไอคอน, empty state):

```
blue-700  #12488F   blue-100  #D3E3FB   blue-50  #EAF2FD
red-600   #D92D20   red-100   #FEE4E2   red-50   #FEF3F2
green-700 #0E7A4F   green-100 #D1FADF   green-50 #ECFDF3
amber-700 #B54708   amber-100 #FEF0C7   amber-50 #FFFAEB
```

**สีกลาง — โทนอุ่น ไม่ใช่เทา** เลิกใช้ `Colors.grey[N]` ทั้งหมด ใช้ 6 ค่านี้แทน:

```
ink   #101828   ตัวอักษรหลัก
ink2  #475467   ตัวอักษรรอง
ink3  #667085   ตัวอักษร/ไอคอนจาง, placeholder
line  #E6E3DC   เส้นขอบ, ตัวคั่น
bg    #F8F6F2   พื้นหลังจอ  (ครีม ไม่ใช่เทา)
bg2   #EFEDE6   พื้นปุ่มไอคอน, avatar เปล่า
```

พื้นหลังเป็นครีมอมน้ำตาลแทนเทาอมฟ้า เพราะจอนี้ครอบครัวเปิดค้างไว้ทั้งวัน โทนอุ่นล้าตาน้อยกว่า
และทำให้จอไม่ดูเหมือนเครื่องมือแพทย์

`Colors.grey[200]` / `[300]` / `[400]` ที่กระจายอยู่ตอนนี้ กลายเป็น `line` หรือ `bg2` แล้วแต่หน้าที่ —
ปัญหาของ `grey[N]` คือมันไม่บอกว่าตัวเองเป็นเส้นขอบหรือพื้นหลัง คนแก้คนต่อไปจึงต้องเดา

---

## 3. ตัวอักษร

**ฟอนต์: `Noto Sans Thai` คู่กับ `Inter`** — จำเป็นจริง ไม่ใช่ของแต่ง เพราะ
`sos_contact_screen.dart` มีชื่อไทยฮาร์ดโค้ดอยู่แล้ว (`คุณป้ามานี`, `พี่เขียว`) และ
`ว่าง` / `ไม่ว่าง` เป็น label ที่ผู้ป่วยต้องอ่าน ถ้าไม่ประกาศฟอนต์ไทย มันจะ fallback
ไปน้ำหนักที่ไม่ตรงกับ Latin แล้วบรรทัดเดียวกันจะดูหนักไม่เท่ากัน

`pubspec.yaml` — จะใช้ `google_fonts` หรือ bundle เองก็ได้ แล้วแต่คุณ

| ระดับ | ขนาด/น้ำหนัก | ใช้ที่ |
|---|---|---|
| Display | 26 / w700 | หัวข้อจอผู้ป่วย ("Where do you want to go?") |
| H1 | 26 / w700 | ชื่อจอ, ชื่อผู้ใช้ |
| H2 | 21 / w700 | หัวข้อรอง |
| Large | 18 / w600 | ชื่อสถานที่, ตัวอักษรบนปุ่ม |
| Body | 15 / w400 | ข้อความทั่วไป |
| Small | 13 / w400 | metadata ("2.0 km · 35°C") |
| Label | 12 / w600 uppercase | หัวคอลัมน์ ("RISK SCORE") |

**ขนาดฝั่งผู้ป่วยห้ามลด** ของเดิมใช้ 18–24px อยู่แล้ว ซึ่งเป็นสัญชาตญาณที่ถูก

---

## 4. ระยะ มุม เงา

```
spacing  4 · 8 · 12 · 16 · 20 · 24 · 32     (เลิกใช้ SizedBox(height: 5) และ 26)
radius   10 เล็ก · 14 กลาง · 20 การ์ดใหญ่ · 999 วงกลม
```

**ปุ่มสูงขั้นต่ำ 52px** (เดิม `minimumSize: Size(0, 48)`) — 48 คือขั้นต่ำของ Material
สำหรับคนทั่วไป กลุ่มเป้าหมายนี้มือสั่น

เงา 3 ระดับ แทน `elevation: 3` + `shadowColor: Colors.black` ที่ใช้อยู่ใน
`caregiver_homepage_screen.dart` — สังเกตว่าเงาเป็น **น้ำตาลอ่อน ไม่ใช่ดำ** เพื่อให้เข้ากับพื้นครีม:

```
sh1  0 1px 2px rgba(43,34,20,.05), 0 1px 3px rgba(43,34,20,.07)      ปุ่ม, การ์ดปกติ
sh2  0 6px 14px -4px rgba(43,34,20,.10)                              แถบบอกทาง, ปุ่มลอย
sh3  0 26px 50px -18px rgba(43,34,20,.26)                            dialog
```

---

## 5. ร่าง `ThemeData` — ⚠️ ยังไม่เคย compile

ไฟล์ใหม่ `lib/theme/pathguard_theme.dart` แล้วส่งเข้า `MaterialApp(theme: ...)` ใน `main.dart`
**นี่คือการแก้จุดเดียวที่ให้ผลกับทุกจอพร้อมกัน ทำอันนี้ก่อนอย่างอื่น**

```dart
// ⚠️ ร่าง — เขียนบนเครื่องที่ไม่มี Flutter ยังไม่เคย analyze หรือรัน
import 'package:flutter/material.dart';

class PG {
  static const blue700  = Color(0xFF12488F);
  static const blue600  = Color(0xFF1D63C9);
  static const blue50   = Color(0xFFEAF2FD);
  static const red600   = Color(0xFFD92D20);
  static const red50    = Color(0xFFFEF3F2);
  static const green700 = Color(0xFF0E7A4F);
  static const green50  = Color(0xFFECFDF3);
  static const amber700 = Color(0xFFB54708);
  static const amber50  = Color(0xFFFFFAEB);
  static const nav      = Color(0xFF325F44); // ค่าเดิมจาก navigation_screen.dart
  static const ink      = Color(0xFF101828);
  static const ink2     = Color(0xFF475467);
  static const ink3     = Color(0xFF667085);
  static const line     = Color(0xFFE6E3DC);
  static const bg       = Color(0xFFF8F6F2);
  static const bg2      = Color(0xFFEFEDE6);
}

final pathguardTheme = ThemeData(
  useMaterial3: true,
  scaffoldBackgroundColor: PG.bg,
  colorScheme: const ColorScheme.light(
    primary: PG.blue600,
    onPrimary: Colors.white,
    error: PG.red600,
    surface: Colors.white,
    onSurface: PG.ink,
  ),
  // fontFamily: 'NotoSansThai',   // หรือ GoogleFonts.notoSansThaiTextTheme()

  appBarTheme: const AppBarTheme(
    backgroundColor: Colors.white,
    foregroundColor: PG.ink,
    elevation: 0,
    scrolledUnderElevation: 0,
    centerTitle: false,
    titleTextStyle: TextStyle(
      fontSize: 19, fontWeight: FontWeight.w700, color: PG.ink),
  ),

  elevatedButtonTheme: ElevatedButtonThemeData(
    style: ElevatedButton.styleFrom(
      backgroundColor: PG.blue600,
      foregroundColor: Colors.white,
      minimumSize: const Size(0, 52),
      textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      elevation: 1,
    ),
  ),

  cardTheme: CardThemeData(
    color: Colors.white,
    elevation: 1,
    shadowColor: const Color(0x142B2214),
    shape: RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(14),
      side: const BorderSide(color: PG.line),
    ),
  ),

  inputDecorationTheme: InputDecorationTheme(
    filled: true,
    fillColor: Colors.white,
    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
    border: OutlineInputBorder(
      borderRadius: BorderRadius.circular(14),
      borderSide: const BorderSide(color: PG.line, width: 1.5)),
    enabledBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(14),
      borderSide: const BorderSide(color: PG.line, width: 1.5)),
    focusedBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(14),
      borderSide: const BorderSide(color: PG.blue600, width: 2)),
    hintStyle: const TextStyle(color: PG.ink3, fontSize: 16),
  ),

  textTheme: const TextTheme(
    headlineMedium: TextStyle(fontSize: 26, fontWeight: FontWeight.w700, color: PG.ink),
    titleLarge:     TextStyle(fontSize: 21, fontWeight: FontWeight.w700, color: PG.ink),
    titleMedium:    TextStyle(fontSize: 18, fontWeight: FontWeight.w600, color: PG.ink),
    bodyLarge:      TextStyle(fontSize: 15, height: 1.5, color: PG.ink),
    bodyMedium:     TextStyle(fontSize: 13, height: 1.45, color: PG.ink2),
  ),
);
```

`main.dart`:

```dart
return MaterialApp(
  theme: pathguardTheme,      // <- บรรทัดเดียวนี้เปลี่ยนทุกจอ
  home: const LoginScreen(),
);
```

---

## 6. ทีละจอ — เปลี่ยนแค่ style ไม่แตะ logic

### `login_screen.dart`

`CustomLoginButton` รับ `backgroundColor` เข้ามาอยู่แล้ว **โครงสร้างไม่ต้องแตะ ส่งค่าใหม่เข้าไปแทน**

`Colors.grey[400]!` → Caregiver ใช้ `PG.blue600` ตัวหนังสือขาว ส่วน Patient ใช้พื้นขาวขอบน้ำเงิน
(secondary) เพื่อให้เห็นว่าอันไหนคือทางหลัก · เพิ่มโลโก้บนสุด · การ์ดขาวลอยกลางจอเอาออกได้
ใช้พื้นจอเต็มแทน จะโล่งกว่า

### `caregiver_login_screen.dart`

🔴 **จอนี้ต้องรื้อก่อนตกแต่ง ไม่ใช่ตกแต่งก่อนรื้อ** — `EmailTextField` และ `PasswordTextField`
เป็น `const StatelessWidget` ครอบ `TextField` เปล่า **ไม่มี `controller`** และปุ่ม Login ทำแค่
`Navigator.push` แปลว่า **จอนี้อ่านสิ่งที่ผู้ใช้พิมพ์ไม่ได้เลย** ทำให้รับค่าได้ก่อน แล้วค่อยแต่ง

ตอนแต่ง: `widthFactor: 0.7` เปลี่ยนเป็น padding 28 ซ้ายขวา (0.7 บนจอกว้างจะบีบเกินไป) ·
`decorationStyle: wavy` ใต้ SIGN UP เอาออก ใช้สีน้ำเงิน w700 แทน

### `form_login_screen.dart`

`QuestionCard` ใช้ต่อได้ตามเดิม เปลี่ยนแค่ label เป็น 14/w600 สี `ink2`

### `caregiver_homepage_screen.dart`

หัวจอ `Colors.grey[300]` → พื้นขาว + เส้นคั่น `line` ข้างล่าง · badge จุดแดงบนกระดิ่งเก็บไว้
เติมขอบขาว 2px รอบจุดให้เห็นชัดขึ้น · แถวผู้ป่วยครอบด้วย `Card` · เพิ่ม pill `Level 1` / `Level 2`
ใต้ชื่อ (ข้อมูลมีอยู่แล้วใน `result['state']`) · empty state ใส่ไอคอนในวงกลม `blue50`

**dialog รหัสจับคู่**: ตอนนี้เอารหัสยัดในประโยคเดียวกับข้อความ แต่รหัสนี้ต้องถูก
**อ่านออกเสียงทางโทรศัพท์** แยกออกมาเป็นกล่องเอกเทศ ฟอนต์ monospace ระยะตัวอักษรกว้าง
ขนาด 28 จะลดการอ่านผิดระหว่าง `0`/`O` และ `1`/`I`

### `add_patient_screen.dart`

รูปแบบคำถามมีเลขกำกับ **ดีอยู่แล้ว เก็บไว้** · ทำเลขข้อเป็นสีน้ำเงิน · ปุ่ม Confirm ย้ายไปตรึง
ล่างจอบนแถบขาวมีเส้นคั่น (ฟอร์มยาว ผู้ใช้ไม่ต้องเลื่อนหาปุ่ม) · แถว `FamiliarPlaceInput`
ครอบด้วย Card + pill เลขลำดับ

⚠️ **อ่านข้อ 8.1 ก่อนแตะจอนี้** — มันเก็บของสำคัญไว้แล้วทิ้ง

### `track_screen.dart`

จอนี้ได้ประโยชน์จากการตกแต่งมากที่สุด ตอนนี้เป็น `Text` สี่บรรทัดซ้อนกัน ไม่มีลำดับความสำคัญ

Risk score ขึ้นเป็นตัวเลข 38px + pill (Low/Medium/High) + แถบ progress สีตามระดับ
ที่เหลือเป็นแถว icon–label–value · **เมื่อ risk สูง เพิ่มแถบแดงใต้ AppBar** บอกว่าแจ้งเตือนไปแล้ว
เมื่อไหร่ พร้อมปุ่มโทรหาผู้ป่วย · เกณฑ์ `>80 High` / `>=50 Medium` ในโค้ดตรงกับฝั่ง backend แล้ว
ไม่ต้องแก้

### `notification_screen.dart`

ประโยค `'... want to ask to go to ...'` ควรเป็น `wants to go to` · ทำชื่อสถานที่เป็นสีน้ำเงินตัวหนา ·
เพิ่มเวลาที่ขอ + ระยะทาง · ปุ่ม Approve/Reject กว้างเท่ากันอยู่แล้ว **ถูกแล้ว เก็บไว้** ·
empty state ตอนนี้เป็น `Text('No notifications')` เปล่า ๆ ใส่ไอคอน + ประโยคที่อุ่นกว่านี้

### `patient_level1_screen/patient_homepage_screen.dart`

การ์ด `Colors.grey[200]` → การ์ดขาวขอบ `line` + ไอคอนหมุดในวงกลม `blue50` ·
`'Hello!\n${name}'` แยกเป็นสอง `Text` จริง (Hello เล็กจาง / ชื่อใหญ่หนา) แทนการใช้ `\n` ·
ช่องค้นหา `borderRadius: 100` เก็บไว้ ถูกแล้ว

**waiting / rejected**: ไอคอน 120px เปลืองที่และดูโดดเดี่ยว ใส่ในวงกลมพื้นอ่อน 132px
(`blue50` / `amber50`) แล้วเพิ่มประโยครองใต้หัวข้อ — ผู้ป่วยกำลังยืนรออยู่ ต้องรู้ว่าต้องทำอะไรต่อ

### `patient_level2_screen/patient_homepage_screen.dart`

**3 ที่ และไม่มีช่องค้นหา — ถูกแล้ว อย่าเปลี่ยน** มีเทสต์ฝั่ง backend ล็อกไว้ ·
ไทล์ 120px เก็บไว้ ใส่ไอคอนในกล่องมนแทนไอคอนลอย · AppBar `'Hello, ${name}'` ย้ายลงมา
เป็นหัวจอจริงจะอ่านง่ายกว่าอยู่บน AppBar เล็ก ๆ

### `patient_level1_screen/navigation_screen.dart`

แถบคำสั่ง `#325F44` **เก็บสีเดิม** เพิ่มมุมมน 14 + เงา sh2 · แยกลำดับระหว่างระยะทางกับคำสั่ง
ให้ชัดขึ้น (ระยะ 19px จาง / คำสั่ง 21px w700 ขาว)

⚠️ **ปุ่ม SOS กับปุ่ม recenter อาจทับกัน** — SOS อยู่ `bottom: 30` กลางจอ ส่วน
`floatingActionButton` เป็น `endFloat` ซึ่งอยู่แถวล่างเหมือนกัน ใน prototype ย้าย recenter
ขึ้นไปเหนือ SOS ชิดขวา ลองบนเครื่องจริงด้วย

### `patient_level2_screen/navigation_screen.dart`

เข็มทิศ 180px ใส่ในวงกลม `blue50` 230px · ตอนถึงที่หมายเปลี่ยนทั้งวงเป็น `green50` +
เช็คถูกเขียว — ผู้ป่วยระดับ 2 ต้องเห็นความต่างจากระยะไกล

### `sos_contact_screen.dart`

วงกลม SOS ซ้อนสามชั้นที่ทำไว้ **ดีมาก เก็บไว้ทั้งดุ้น** · การ์ดผู้ดูแล: ตอน `busy` ให้หรี่ทั้งใบ
(opacity .55) ไม่ใช่แค่เปลี่ยนสีตัวอักษร — คนแก่มองปราดเดียวต้องแยกออก ·
`ว่าง`/`ไม่ว่าง` ทำเป็น pill พื้นอ่อน

---

## 7. ห้ามเปลี่ยน — พร้อมเหตุผล

1. **ปุ่ม SOS** — ขนาด รูปทรง ตำแหน่ง คงเดิมทุกจอผู้ป่วย ห้ามทำให้กลมกลืนกับปุ่มอื่น
   และห้ามใส่ animation ที่หน่วงการกด
2. **สีแดง = ฉุกเฉินเท่านั้น** ห้ามใช้ตกแต่ง ห้ามใช้เป็นสีเน้น
3. **Level 2 ได้ 3 ที่ ไม่มีช่องค้นหา** — ฝั่ง backend มีเทสต์ล็อกไว้
   (`test_home_screen_shows_three_places_at_every_stage`) และตัวเลข 3 มาจากข้อเสนอของคุณเอง
   ที่ชนะเหตุผลในรายงาน
4. **ขนาดตัวอักษรฝั่งผู้ป่วยห้ามลด** และห้ามใช้ตัวอักษรจางเพื่อความสวย
5. **ห้ามเอา state ออก** — waiting, rejected, empty, error มีครบแล้ว เพิ่มความสวยได้ ตัดไม่ได้
6. **`#325F44`** ของแถบบอกทาง เป็นค่าที่เลือกไว้แล้ว

---

## 8. สองเรื่องที่ดีไซน์แก้ให้ไม่ได้ — สำคัญกว่าการตกแต่งทั้งหมดรวมกัน

### 8.1 หมุดที่ Add Patient เก็บ ถูกทิ้งทั้งหมด

`add_patient_screen.dart:567` pop ค่าออกมาเป็น
`{name, state, home, otherPlaces, profileImage}` โดย `home` เป็น `ParsedLocation` และ
`otherPlaces` เป็น `[{name, location}]` — **ตรงกับรูปแบบที่ `POST /api/patients/{id}/places`
ต้องการพอดี**

แต่ `caregiver_homepage_screen.dart:52` อ่านแค่ `name` กับ `state` แล้วส่งไป
`POST /api/patients` **หมุดไม่เคยออกจากเครื่อง**

ผลที่วัดมาแล้วด้วย pipeline จริง (Module 3 + น้ำหนักจาก KB):

| ผู้ป่วยอยู่ที่ไหน | ปักแค่บ้าน | ปักครบ 4 ที่ |
|---|---|---|
| ที่บ้าน | 9.0 low | 9.0 low |
| **วัดที่ไปทุกวัน** | **56.0 medium** | 15.0 low |
| **ตลาดที่ไปทุกวัน** | **56.0 medium** | 15.0 low |
| บ้านลูกสาว | **56.0 medium** | 18.0 low |
| หลงห่างบ้าน 2.5 กม. | 56.0 medium | 56.0 medium |

**ปักแค่บ้าน = ระบบแยกไม่ออกระหว่าง "อยู่วัดที่ไปทุกวัน" กับ "หลงอยู่ห่างบ้าน 2.5 กม."**
และกฎ sustained (5 รอบติดที่ >=50 ที่ throttle 60 วินาที) จะดันแจ้งเตือนครอบครัว
**หลังอยู่วัดประมาณ 5 นาที ทุกครั้งที่ไป** — คือ alert fatigue ที่ทีมพยายามเลี่ยงมาตลอด

จอเก็บข้อมูลถูกแล้ว **ขาดแค่การส่ง** สั้น ๆ คือหลัง `POST /api/patients` สำเร็จ ยิงต่ออีกครั้ง
ไปที่ `POST /api/patients/{id}/places` ด้วยหมุดที่อยู่ในมือแล้ว รายละเอียด endpoint อยู่ใน
`API_CONTRACT_APP.md` §10

### 8.2 จอ login ผู้ดูแลอ่านสิ่งที่พิมพ์ไม่ได้

เขียนไว้ในข้อ 6 แล้ว แต่ย้ำเพราะจอนี้ดู "เสร็จแล้ว" — `EmailTextField` / `PasswordTextField`
เป็น `const` ไม่มี controller ปุ่ม Login แค่ `Navigator.push` **จอนี้รับค่าไม่ได้เลย**
แต่งจอนี้ก่อนรื้อ = แต่งของที่ยังไม่ทำงาน

---

## 9. ลำดับที่แนะนำ

1. `theme/pathguard_theme.dart` + ส่งเข้า `MaterialApp` — แก้จุดเดียว เห็นผลทุกจอ
2. รื้อ `caregiver_login_screen.dart` ให้รับค่าได้ (8.2)
3. **ส่งหมุดขึ้น backend (8.1)** — อันนี้เปลี่ยนคุณภาพของระบบ ไม่ใช่หน้าตา
4. ค่อยไล่ตกแต่งทีละจอตามข้อ 6

ข้อ 1–3 มีค่ามากกว่าข้อ 4 ทั้งหมดรวมกัน ถ้าเวลาไม่พอ ทำสามข้อแรกแล้วหยุด

---

## 10. ถามกลับมาได้

ค่าสีทุกค่าในเอกสารนี้เปลี่ยนได้ ไม่มีอันไหนศักดิ์สิทธิ์ — ที่เปลี่ยนไม่ได้คือ **เกณฑ์ contrast**
กับ **ความหมายของสี** ส่วนเฉดที่แน่นอน ถ้าคุณมีชุดที่ชอบกว่าและผ่าน 4.5:1 ใช้ของคุณได้เลย

โครงสร้างจอเป็นของคุณ เอกสารนี้เป็นข้อเสนอ ไม่ใช่คำสั่ง
