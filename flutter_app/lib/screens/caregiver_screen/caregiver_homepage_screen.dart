import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'dart:async';
import 'dart:io';
import 'add_patient_screen.dart';
import 'track_screen.dart';
import 'notification_screen.dart';
import 'missing_patient_screen.dart';
import 'sos_alert_screen.dart';
import 'invite_caregiver_screen.dart';
import 'join_patient_screen.dart';
import 'dart:convert';
import '../../services/api_client.dart';
import '../../services/location_service.dart';
import '../../services/alert_navigation.dart';
import '../../services/caregiver_location_service.dart';
import '../../services/caregiver_session.dart';
import '../../services/trip_request_directory.dart';
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
  Timer? _locationTicker;
  /// Three states, and the third is why this is nullable: null means this
  /// caregiver has never answered the question, which is not the same claim as
  /// "unavailable" and must not be drawn as one. Matches the column behind it.
  bool? _isAvailable;

  /// Reads the caregiver's own availability back on launch.
  ///
  /// This used to be a bare `= true` with nothing behind it, so the pill said
  /// "Available" while the row said NULL and the patient's SOS screen — reading
  /// the same field from the other end — said "Unknown status". Two screens,
  /// one fact, and this was the screen that was wrong.
  ///
  /// Writing a default on launch instead would have been worse, not simpler:
  /// it asserts on the caregiver's behalf exactly what the column is nullable
  /// to avoid, and it would overwrite an explicit "unavailable" every time they
  /// reopened the app.
  Future<void> _loadOwnAvailability() async {
    try {
      final res = await apiGet('/api/me');
      if (res.statusCode != 200) return;
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      if (mounted) setState(() => _isAvailable = body['is_available'] as bool?);
    } catch (_) {
      // Left unset on purpose. "We could not ask" and "they have not answered"
      // both render honestly as "Not set"; guessing either way is the bug this
      // method exists to remove.
    }
  }

  Future<void> _updateAvailability(bool value) async {
    final previous = _isAvailable;
    setState(() => _isAvailable = value);
    try {
      final id = CaregiverSession.instance.caregiverId;
      if (id == null) return;
      final res = await apiPut('/api/caregivers/$id/availability', body: {'is_available': value});
      if (res.statusCode != 200 && mounted) {
        setState(() => _isAvailable = previous);
      }
    } catch (_) {
      if (mounted) setState(() => _isAvailable = previous);
    }
  }

  @override
  void initState() {
    super.initState();
    _loadPatients();
    _loadOwnAvailability();
    // Keeps the "expires in Xh Ym" label live and refreshes each patient's
    // risk badge — cheap enough to just always tick.
    _countdownTicker = Timer.periodic(const Duration(minutes: 1), (_) {
      if (!mounted) return;
      setState(() {});
      _refreshRiskLevels();
    });
    // Where this caregiver is, for the patient's SOS ranking. Its own timer
    // rather than a share of the one above: the ranking sorts on freshness
    // before distance and expires a position at 1800 s, so five minutes keeps
    // it comfortably inside that at a twelfth of the GPS fixes a minute-tick
    // would take. Foreground only — this screen is on, or nothing is sent.
    reportCaregiverLocation();
    _locationTicker = Timer.periodic(
      const Duration(minutes: 5), (_) => reportCaregiverLocation());
    // Redraws the notification bell's badge the instant a trip request
    // appears/gets resolved, instead of only reflecting it on next rebuild.
    TripRequestDirectory.instance.addListener(_onTripRequestsChanged);
  }

  @override
  void dispose() {
    _countdownTicker?.cancel();
    _locationTicker?.cancel();
    TripRequestDirectory.instance.removeListener(_onTripRequestsChanged);
    super.dispose();
  }

  void _onTripRequestsChanged() {
    if (mounted) setState(() {});
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
      return 'Code: $code\nExpired';
    }
    final remaining = expiresAt.difference(DateTime.now());
    final hours = remaining.inHours;
    final minutes = remaining.inMinutes % 60;
    final remainingLabel = hours > 0 ? '${hours}h ${minutes}m' : '${minutes}m';
    return 'Code: $code\nExpires in $remainingLabel';
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
        final id = p['id'] as int;
        final results = await Future.wait([_fetchHomePlace(id), _fetchRiskLevel(id)]);
        return {...p, 'home': results[0], 'riskLevel': results[1]};
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
          const SnackBar(content: Text('Could not generate a new code, try again')),
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
          title: const Text('New code ready'),
          content: Text(
            'Give this code to the patient to log in again:\n\n${data['pairing_code']}',
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
        const SnackBar(content: Text('Could not connect to the server')),
      );
    }
  }

  /// Shown once when the app is opened. Since 2026-09-06 it is no longer the
  /// only way in — `main.dart`'s push listener opens the same screens the
  /// moment an alert arrives — so this is the catch-up pass for anything that
  /// fired while the app was closed, not the primary path it used to be.
  ///
  /// Queues one full-screen alert per patient with an active unresolved alert,
  /// shown one after another. `gps_loss` routes to MissingPatientScreen
  /// (Module 4, no manual form — auto-activates and summarizes); the rest go
  /// to SosAlertScreen (distance ranking + claim).
  ///
  /// The type strings come from `alert_navigation.dart` rather than living
  /// here, because the push listener has to make the same decision and the two
  /// must not drift. They drifted once already: this method matched
  /// `gps_lost`, which the backend has not written since `3fe7896`, so the
  /// Module 4 screen could never open at all.
  Future<void> _checkForActiveAlerts() async {
    for (final patient in patients) {
      try {
        final res = await apiGet('/api/patients/${patient['id']}/alerts');
        if (res.statusCode != 200) continue;
        final alerts = (jsonDecode(res.body)['alerts'] as List).cast<Map<String, dynamic>>();
        final unresolved = alerts.where((a) => a['resolved'] == false);

        final missing = unresolved.where((a) => a['alert_type'] == missingAlertType);
        if (missing.isNotEmpty) {
          if (!mounted) return;
          await Navigator.push(
            context,
            MaterialPageRoute(
              builder: (context) => MissingPatientScreen(patient: patient, alert: missing.first),
            ),
          );
        }

        final active = unresolved.where((a) => urgentAlertTypes.contains(a['alert_type']));
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

  /// GET .../risk/latest — read-only, safe to poll (never GET /api/risk/{id}
  /// here: that one recomputes and can write an alert on every call). Null
  /// means "no risk score yet" (brand-new patient) — the badge is simply
  /// omitted rather than shown as a false "low risk".
  Future<String?> _fetchRiskLevel(int patientId) async {
    try {
      final res = await apiGet('/api/patients/$patientId/risk/latest');
      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      if (body['status'] != 'ok') return null;
      return body['risk_level'] as String?;
    } catch (_) {
      return null;
    }
  }

  Future<void> _refreshRiskLevels() async {
    for (final patient in patients) {
      final level = await _fetchRiskLevel(patient['id'] as int);
      if (mounted) setState(() => patient['riskLevel'] = level);
    }
  }

  Widget _buildEmptyState() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Container(
              width: 96,
              height: 96,
              decoration: BoxDecoration(
                color: Colors.blue[50],
                shape: BoxShape.circle,
              ),
              alignment: Alignment.center,
              child: Icon(Icons.person_add_alt_1_rounded, size: 48, color: Colors.blue[700]),
            ),
            const SizedBox(height: 20),
            const Text(
              'No patients yet',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.bold,
                color: Colors.black87,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Add a patient to start tracking their location and safety status.',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w400,
                color: Colors.grey[600],
                height: 1.4,
              ),
            ),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              height: 56,
              child: ElevatedButton.icon(
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
                    'place_name': 'Home',
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
                  foregroundColor: Colors.white,
                  elevation: 0,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                ),
                icon: const Icon(Icons.add_rounded, size: 24),
                label: const Text(
                  'Add Patient',
                  style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
                ),
              ),
            ),
            const SizedBox(height: 12),
            // The second caregiver's way in. Sits beside Add Patient rather
            // than behind a per-patient menu, since the person who needs it
            // has an empty list — there is no patient card to open a menu on.
            SizedBox(
              width: double.infinity,
              height: 52,
              child: OutlinedButton.icon(
                onPressed: () async {
                  final joined = await Navigator.push(
                    context,
                    MaterialPageRoute(builder: (context) => const JoinPatientScreen()),
                  );
                  if (joined == true && mounted) _loadPatients();
                },
                style: OutlinedButton.styleFrom(
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                ),
                icon: const Icon(Icons.group_add_rounded, size: 22),
                label: const Text(
                  'Join a patient',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// A small pill showing the pairing code + expiry state. Color signals
  /// urgency at a glance: red means the code needs regenerating before the
  /// patient can log in, blue means it's still usable.
  Widget _buildPairingCodeChip(Map<String, dynamic> patient) {
    final label = _pairingCodeLabel(patient);
    if (label.isEmpty) return const SizedBox.shrink();
    final expired = _pairingCodeIsExpired(patient);
    final color = expired ? Colors.red[700]! : Colors.blue[700]!;
    final background = expired ? Colors.red[50]! : Colors.blue[50]!;
    return Container(
      margin: const EdgeInsets.only(top: 6),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            expired ? Icons.error_outline_rounded : Icons.vpn_key_rounded,
            size: 15,
            color: color,
          ),
          const SizedBox(width: 6),
          Flexible(
            child: Text(
              label.replaceAll('\n', ' • '),
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: color),
            ),
          ),
        ],
      ),
    );
  }

  /// One patient's card: identity + pairing status up top, a large full-width
  /// "Track" button as the dominant action (this is the one caregivers reach
  /// for constantly), and the pairing-code refresh tucked in as a smaller
  /// secondary action so it doesn't compete for attention.
  /// Color-coded at-a-glance risk state — lets a caregiver scan the whole
  /// list and see who needs attention right now without opening Track for
  /// each patient individually.
  Widget _buildRiskBadge(Map<String, dynamic> patient) {
    final level = patient['riskLevel'] as String?;
    if (level == null) return const SizedBox.shrink();
    final color = level == 'high'
        ? Colors.red[700]!
        : level == 'medium'
            ? Colors.orange[800]!
            : Colors.green[700]!;
    final bg = level == 'high'
        ? Colors.red[50]!
        : level == 'medium'
            ? Colors.orange[50]!
            : Colors.green[50]!;
    final label = level == 'high'
        ? 'High risk'
        : level == 'medium'
            ? 'Medium risk'
            : 'Low risk';
    return Padding(
      padding: const EdgeInsets.only(top: 4, bottom: 4),
      child: Semantics(
        label: '$label for ${patient['name']}',
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
          decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(20)),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
              const SizedBox(width: 6),
              Text(
                label,
                style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: color),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildPatientCard(Map<String, dynamic> patient) {
    final profileImage = patient['profileImage'] as File?;
    final name = patient['name'] as String? ?? 'Patient';
    return Card(
      margin: const EdgeInsets.only(bottom: 14),
      elevation: 1.5,
      shadowColor: Colors.black26,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 12, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                CircleAvatar(
                  radius: 30,
                  backgroundColor: Colors.grey[300],
                  backgroundImage: profileImage != null ? FileImage(profileImage) : null,
                  child: profileImage == null ? Icon(Icons.person, size: 34, color: Colors.grey[800]) : null,
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        name,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w700,
                          color: Colors.black87,
                        ),
                      ),
                      _buildRiskBadge(patient),
                      _buildPairingCodeChip(patient),
                    ],
                  ),
                ),
                Semantics(
                  label: 'Invite another caregiver for $name',
                  button: true,
                  child: IconButton(
                    tooltip: 'Invite another caregiver',
                    icon: const Icon(Icons.person_add_alt_1_rounded),
                    color: Colors.grey[700],
                    onPressed: () {
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (context) => InviteCaregiverScreen(
                            patientId: patient['id'] as int,
                            patientName: name,
                          ),
                        ),
                      );
                    },
                  ),
                ),
                Semantics(
                  label: 'Generate a new pairing code for $name',
                  button: true,
                  child: IconButton(
                    tooltip: 'Generate new pairing code',
                    icon: const Icon(Icons.autorenew_rounded),
                    color: Colors.grey[700],
                    onPressed: () => _regeneratePairingCode(patient),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),
            Semantics(
              label: 'Track $name\'s live location',
              button: true,
              child: SizedBox(
                width: double.infinity,
                height: 52,
                child: ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.blue,
                    foregroundColor: Colors.white,
                    elevation: 0,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                  ),
                  onPressed: () {
                    Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (context) => TrackScreen(patient: patient),
                      ),
                    );
                  },
                  icon: const Icon(Icons.my_location_rounded, size: 22),
                  label: const Text(
                    'Track',
                    style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Top bar: identity, availability, and quick access to notifications and
  /// sign-out. Kept visually calm (no bright colors) since nothing here is an
  /// emergency action — those live one tap away on Track/Notifications.
  Widget _buildHeader(BuildContext context) {
    final pendingCount = TripRequestDirectory.instance.pending.length;
    return Container(
      width: double.infinity,
      color: Colors.grey[200],
      padding: const EdgeInsets.fromLTRB(20, 16, 12, 16),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Welcome back,',
                  style: TextStyle(fontSize: 14, color: Colors.grey[700]),
                ),
                const SizedBox(height: 2),
                Text(
                  widget.caregiverName ?? 'Caregiver',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: Colors.black87),
                ),
              ],
            ),
          ),
          // Restored from GET /api/me on launch — see _loadOwnAvailability.
          // The three labels and colours are the same ones the patient's SOS
          // screen uses for this caregiver, so what a caregiver sees on their
          // own row is what the person in trouble sees on theirs.
          Semantics(
            label: switch (_isAvailable) {
              null => 'Availability not set, tap to say you are available',
              true => 'Available to caregiving requests, tap to go unavailable',
              false => 'Unavailable to caregiving requests, tap to go available',
            },
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(24),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    switch (_isAvailable) {
                      null => 'Not set',
                      true => 'Available',
                      false => 'Unavailable',
                    },
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                      color: switch (_isAvailable) {
                        null => Colors.black45,
                        true => Colors.green[700],
                        false => Colors.red,
                      },
                    ),
                  ),
                  // A Switch has two positions and this has three states, so
                  // the label above carries the truth and the switch shows
                  // "off" until the question has actually been answered.
                  Switch(
                    value: _isAvailable ?? false,
                    activeThumbColor: Colors.green[700],
                    onChanged: _updateAvailability,
                  ),
                ],
              ),
            ),
          ),
          Semantics(
            label: pendingCount > 0
                ? 'Notifications, $pendingCount pending trip request${pendingCount == 1 ? '' : 's'}'
                : 'Notifications',
            button: true,
            child: IconButton(
              onPressed: () {
                Navigator.push(context, MaterialPageRoute(builder: (context) => const NotificationScreen()));
              },
              icon: Badge(
                smallSize: 10,
                backgroundColor: Colors.red,
                isLabelVisible: pendingCount > 0,
                child: const Icon(Icons.notifications_none_rounded, size: 28),
              ),
            ),
          ),
          Semantics(
            label: 'Sign out',
            button: true,
            child: IconButton(
              tooltip: 'Sign out',
              onPressed: () async {
                await FirebaseAuth.instance.signOut();
                await CaregiverSession.instance.clear();
                if (!context.mounted) return;
                Navigator.of(context).pushAndRemoveUntil(
                  MaterialPageRoute(builder: (context) => const LoginScreen()),
                  (route) => false,
                );
              },
              icon: const Icon(Icons.logout_rounded),
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.grey[100],
      body: SafeArea(
        child: Column(
          children: [
            _buildHeader(context),
            Expanded(
              child: _loadingPatients
                  ? const Center(child: CircularProgressIndicator(color: Colors.blue))
                  : patients.isEmpty
                      ? _buildEmptyState()
                      : ListView(
                          padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
                          children: patients.map((p) => _buildPatientCard(p)).toList(),
                        ),
            ),
          ],
        ),
      ),
    );
  }
}