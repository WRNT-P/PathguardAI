import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'dart:io';
import 'add_patient_screen.dart';
import 'track_screen.dart';
import 'notification_screen.dart';
import 'destination_prediction_screen.dart';
import 'find_patient_screen.dart';
import 'invite_caregiver_screen.dart';
import 'join_patient_screen.dart';
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

  @override
  void initState() {
    super.initState();
    _loadPatients();
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
    } catch (_) {
    } finally {
      if (mounted) setState(() => _loadingPatients = false);
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
                  patients.add({...result, 'id': data['patient_id']});
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
          const SizedBox(height: 8),
          // The second caregiver's way in. Sits beside Add Patient rather than
          // behind a menu because the person who needs it has an empty list —
          // there is no patient row to open a menu on.
          OutlinedButton(
            onPressed: () async {
              final joined = await Navigator.push(
                context,
                MaterialPageRoute(builder: (context) => const JoinPatientScreen()),
              );
              if (joined == true && mounted) _loadPatients();
            },
            style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48)),
            child: const Text('Join a patient', style: TextStyle(fontSize: 16)),
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
                      Text('ID: ${patient['id']}',
                      style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w700,
                        color: Colors.black87,
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
                } else if (value == 'invite') {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (context) => InviteCaregiverScreen(
                        patientId: patient['id'] as int,
                        patientName: patient['name'] as String,
                      ),
                    ),
                  );
                }
              },
              itemBuilder: (context) => const [
                PopupMenuItem(value: 'predict', child: Text('คาดการณ์ปลายทาง')),
                PopupMenuItem(value: 'find', child: Text('ค้นหา (หาไม่เจอ)')),
                PopupMenuItem(value: 'invite', child: Text('เชิญผู้ดูแลอีกคน')),
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