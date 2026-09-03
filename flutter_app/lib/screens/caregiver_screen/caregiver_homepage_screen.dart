import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'dart:async';
import 'dart:io';
import 'add_patient_screen.dart';
import 'track_screen.dart';
import 'notification_screen.dart';
import 'destination_prediction_screen.dart';
import 'find_patient_screen.dart';
import 'sos_alert_screen.dart';
import 'dart:convert';
import '../../services/api_client.dart';
import '../../services/location_service.dart';
import '../../services/caregiver_session.dart';
import '../login_screen.dart';

class CaregiverHomePageScreen extends StatefulWidget {
  final String? caregiverName;
  const CaregiverHomePageScreen({super.key, this.caregiverName});

  @override
  State<CaregiverHomePageScreen> createState() => _CaregiverHomePageScreenState();
}


class _CaregiverHomePageScreenState extends State<CaregiverHomePageScreen> {
  List<Map<String, dynamic>> patients = [];
  bool _loadingPatients = true;
  Timer? _countdownTicker;

  @override
  void initState() {
    super.initState();
    _loadPatients();
    // Only needed to keep the "expires in Xh Ym" label live — cheap enough
    // to just always tick, since it's a no-op setState when nothing's shown.
    _countdownTicker = Timer.periodic(const Duration(minutes: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _countdownTicker?.cancel();
    super.dispose();
  }

  /// A pairing code is single-use and expires in 24h — the backend doesn't
  /// keep it around for us to re-fetch once it's been shown, so this only
  /// works for a patient added in this same app session. After a restart
  /// (patients reloaded via GET /api/patients) there's nothing to show.
  bool _pairingCodeIsExpired(Map<String, dynamic> patient) {
    final expiresAt = patient['pairingExpiresAt'] as DateTime?;
    return expiresAt == null || DateTime.now().isAfter(expiresAt);
  }

  String _pairingCodeLabel(Map<String, dynamic> patient) {
    final code = patient['pairingCode'] as String?;
    final expiresAt = patient['pairingExpiresAt'] as DateTime?;
    if (code == null || expiresAt == null) {
      // No code to show (e.g. after an app restart) — the internal id isn't
      // meaningful to a caregiver, so there's nothing useful to display here.
      return '';
    }
    if (_pairingCodeIsExpired(patient)) {
      return 'รหัส $code หมดอายุแล้ว';
    }
    final remaining = expiresAt.difference(DateTime.now());
    final hours = remaining.inHours;
    final minutes = remaining.inMinutes % 60;
    final remainingLabel = hours > 0 ? '$hoursชม. $minutesนาที' : '$minutesนาที';
    return 'รหัส $code (หมดอายุใน $remainingLabel)';
  }

  /// The patient list used to live only in this widget's in-memory state, so
  /// it reset to empty on every app restart even though the patients still
  /// existed on the backend. GET /api/patients rebuilds it from the server,
  /// keyed off the caregiver's own token — no caregiver_id needed.
  Future<void> _loadPatients() async {
    try {
      final res = await apiGet('/api/patients');
      if (res.statusCode != 200) return;
      final data = jsonDecode(res.body);
      final basics = (data['patients'] as List)
          .map((p) => {
                'id': p['patient_id'] as int,
                'name': p['name'] as String,
              })
          .toList();

      // profileImage stays null — that was always local-only, never sent to
      // the backend, so there's nothing to restore it from after a restart.
      final loaded = await Future.wait(basics.map((p) async {
        return {...p, 'home': await _fetchHomePlace(p['id'] as int)};
      }));

      if (!mounted) return;
      setState(() => patients = loaded);
      await _checkForActiveAlerts();
    } catch (_) {
    } finally {
      if (mounted) setState(() => _loadingPatients = false);
    }
  }

  /// Lets a patient get back in after the original code is long spent — the
  /// only other door was signing out of Firebase entirely, which a patient
  /// (no email/password) can never sign back in from on their own.
  Future<void> _regeneratePairingCode(Map<String, dynamic> patient) async {
    try {
      final res = await apiPost('/api/patients/${patient['id']}/pairing-code');
      if (res.statusCode != 201) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('สร้างรหัสใหม่ไม่สำเร็จ ลองอีกครั้ง')),
        );
        return;
      }
      final data = jsonDecode(res.body);
      setState(() {
        patient['pairingCode'] = data['pairing_code'];
        patient['pairingExpiresAt'] = DateTime.parse(data['expires_at'] as String);
      });

      if (!mounted) return;
      showDialog(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('รหัสใหม่พร้อมแล้ว'),
          content: Text(
            'ให้รหัสนี้กับผู้ป่วยเพื่อล็อกอินใหม่:\n\n${data['pairing_code']}',
            style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('OK'),
            ),
          ],
        ),
      );
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('เชื่อมต่อเซิร์ฟเวอร์ไม่ได้')),
      );
    }
  }

  static const _urgentAlertTypes = {'sos', 'emergency', 'geofence'};

  /// Shown once when the app is opened, not a true full-screen-intent
  /// notification (would need a native foreground service to interrupt a
  /// closed app) — the user decided that's out of scope for now. Queues one
  /// SosAlertScreen per patient with an active unresolved urgent alert, most
  /// recent first per patient, shown one after another.
  Future<void> _checkForActiveAlerts() async {
    for (final patient in patients) {
      try {
        final res = await apiGet('/api/patients/${patient['id']}/alerts');
        if (res.statusCode != 200) continue;
        final alerts = (jsonDecode(res.body)['alerts'] as List).cast<Map<String, dynamic>>();
        final active = alerts.where((a) =>
            a['resolved'] == false && _urgentAlertTypes.contains(a['alert_type']));
        if (active.isEmpty) continue;

        if (!mounted) return;
        await Navigator.push(
          context,
          MaterialPageRoute(
            builder: (context) => SosAlertScreen(
              patientId: patient['id'] as int,
              patientName: patient['name'] as String,
              alert: active.first,
            ),
          ),
        );
      } catch (_) {
      }
    }
  }

  /// Best-effort — a patient with no home pin yet, or a request that fails,
  /// just means the track screen shows "Safe place not set" like it already
  /// does for a brand-new patient.
  Future<ParsedLocation?> _fetchHomePlace(int patientId) async {
    try {
      final res = await apiGet('/api/patients/$patientId/places');
      if (res.statusCode != 200) return null;
      final places = jsonDecode(res.body)['places'] as List;
      final home = places.cast<Map<String, dynamic>>().firstWhere(
            (p) => p['is_home'] == true,
            orElse: () => {},
          );
      if (home.isEmpty) return null;
      return ParsedLocation(
        (home['latitude'] as num).toDouble(),
        (home['longitude'] as num).toDouble(),
      );
    } catch (_) {
      return null;
    }
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.person_add_outlined, size: 56, color: Colors.grey[700]),
          const SizedBox(height: 12),
          FractionallySizedBox(
            widthFactor: 0.7,
            child: Text(
              'No patient added yet.',
              textAlign: TextAlign.center,
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w400,
                color: Colors.black87,
              ),
            ),
          ),
          const SizedBox(height: 12),
          ElevatedButton(
            onPressed: () async {
              final result = await Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (context) => const AddPatientScreen(),
                ),
              );

              if (result != null) {
                final severityLevel = (result['state'] as String).startsWith('2') ? 2 : 1;

                final response = await apiPost('/api/patients', body: {
                  'name': result['name'],
                  'severity_level': severityLevel,
                  'caregiver_id': CaregiverSession.instance.caregiverId,
                });

                 if (!mounted) return;

                if (response.statusCode != 201) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Could not add patient, try again')),
                  );
                  return;
                }

                final data = jsonDecode(response.body);
                final patientId = data['patient_id'] as int;
                final places = <Map<String, dynamic>>[];

                final home = result['home'] as ParsedLocation?;

                if (home != null) {
                  places.add({
                    'place_name': 'บ้าน',
                    'latitude': home.latitude,
                    'longitude': home.longitude,
                    'visit_rank': 'daily_live',
                    'stay_rank': 'all_day',
                    'is_home': true,
                  });
                }
                for (final place in (result['otherPlaces'] as List)) {
                  final loc = place['location'] as ParsedLocation;
                  places.add({
                    'place_name': place['name'] as String,
                    'latitude': loc.latitude,
                    'longitude': loc.longitude,
                    'visit_rank': 'most_days',
                    'stay_rank': 'few_hours',
                    'is_home': false,
                  });
                }

                if (places.isNotEmpty) {
                   final placesResponse = await apiPost('/api/patients/$patientId/places', body: {'places': places});
                   if (placesResponse.statusCode != 201 && mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Patient added, but saving places failed')),
                    );
                   }
                }

                if (!mounted) return;

                setState(() {
                  patients.add({
                    ...result,
                    'id': data['patient_id'],
                    'pairingCode': data['pairing_code'],
                    'pairingExpiresAt': DateTime.parse(data['expires_at'] as String),
                  });
                });

                showDialog(
                  context: context,
                  builder: (context) => AlertDialog(
                    title: const Text('Patient Added'),
                    content: Text(
                      'Give this code to the patient to log in:\n\n${data['pairing_code']}',
                      style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                    ),
                    actions: [
                      TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: const Text('OK'),
                      ),
                    ],
                  ),
                );
              }
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: Colors.blue,
              minimumSize: const Size(0, 48),
            ),
            child: const Text(
              'Add Patient',
              style: TextStyle(color: Colors.white, fontSize: 16,),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPatientRow(Map<String, dynamic> patient) {
    final profileImage = patient['profileImage'] as File?;
    return InkWell(
      onTap: () {

      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Expanded(
              child: Row(
                children: [
                  CircleAvatar(
                    radius: 30, // Adjust size
                    backgroundColor: Colors.grey[300],
                    backgroundImage: profileImage != null ? FileImage(profileImage) : null,
                    child: profileImage == null ? Icon(Icons.person,size: 35, color: Colors.grey[800]): null,
                  ),
                  const SizedBox(width: 12),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(patient['name'],
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                        color: Colors.black87,
                      ),
                      ),
                      if (_pairingCodeLabel(patient).isNotEmpty)
                        Text(_pairingCodeLabel(patient),
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w700,
                          color: _pairingCodeIsExpired(patient) ? Colors.red : Colors.black87,
                        ),
                        ),
                    ],
                  ),
                ],
              ),
            ),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.blue,
                foregroundColor: Colors.white,
                elevation: 3,
                shadowColor: Colors.black,
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
              ),
              onPressed: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (context) => TrackScreen(patient: patient)
                  )
                );
              },
              child: const Text('Track'),
            ),
            IconButton(
              tooltip: 'สร้างรหัสจับคู่ใหม่',
              icon: const Icon(Icons.autorenew),
              onPressed: () => _regeneratePairingCode(patient),
            ),
            PopupMenuButton<String>(
              onSelected: (value) {
                if (value == 'predict') {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (context) => DestinationPredictionScreen(
                        patientId: patient['id'] as int,
                        patientName: patient['name'] as String,
                      ),
                    ),
                  );
                } else if (value == 'find') {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (context) => FindPatientScreen(patient: patient),
                    ),
                  );
                }
              },
              itemBuilder: (context) => const [
                PopupMenuItem(value: 'predict', child: Text('คาดการณ์ปลายทาง')),
                PopupMenuItem(value: 'find', child: Text('ค้นหา (หาไม่เจอ)')),
              ],
            ),
          ]
        ),
      ),
    );
  }

  
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            Container(
              color: Colors.grey[300],
              padding: const EdgeInsets.only(left: 20.0, right: 20.0, top: 12.0, bottom: 12.0),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children:
                    [
                      Text(
                        'Welcome Back,',
                        style: TextStyle(fontSize: 16, color: Colors.black87),
                      ),
                      SizedBox(height: 4),
                      Text(
                        widget.caregiverName ?? 'Caregiver',
                        style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.black87),
                      ),
                    ],
                  ),
                  Row(
                    children: [
                      IconButton(
                        onPressed: () {
                          Navigator.push(context, MaterialPageRoute(builder: (context) => const NotificationScreen()));
                        },
                        icon: const Badge(
                          smallSize: 10,
                          backgroundColor: Colors.red,
                          child: Icon(Icons.notifications_none_outlined, size: 28),
                        ),
                      ),
                      IconButton(
                        onPressed: () async {
                          await FirebaseAuth.instance.signOut();
                          await CaregiverSession.instance.clear();
                          if (!context.mounted) return;
                          Navigator.of(context).pushAndRemoveUntil(
                            MaterialPageRoute(builder: (context) => const LoginScreen()),
                            (route) => false,
                          );
                        },
                        icon: const Icon(Icons.logout),
                      ),
                    ],
                  ),
                ],
              ),
            ),

            // 2. Main content area (scrollable)
            Expanded(
              child: _loadingPatients
                  ? const Center(child: CircularProgressIndicator())
                  : patients.isEmpty
                  ? _buildEmptyState()
                  : SingleChildScrollView(
                      child: Column(
                        children: patients.map((p) => _buildPatientRow(p)).toList(),
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }
}