import 'dart:convert';
import 'package:flutter/material.dart';

import 'api_client.dart';
import 'caregiver_session.dart';
import '../screens/caregiver_screen/sos_alert_screen.dart';
import '../screens/caregiver_screen/missing_patient_screen.dart';

/// Alert types that take over the screen, and where each one goes.
///
/// These live here rather than inside a screen because two places decide it —
/// the push listener in `main.dart` and the poll in
/// `caregiver_homepage_screen.dart` — and a caregiver whose app happened to be
/// open must not be routed differently from one who had it closed.
///
/// ⚠️ Every string here is an `alert_type` the **backend actually writes**.
/// `emergency_decision_engine.py` emits `geofence` and `emergency`;
/// `risk.py:274` emits `gps_loss`; `sos.py` emits `sos`. There is no
/// `wandering` row and there never has been — wandering reaches a caregiver as
/// `emergency` (sustained risk) or `geofence` (danger zone), so those two are
/// what the off-route popup has to watch for.
const urgentAlertTypes = {'sos', 'emergency', 'geofence'};

/// The patient has gone quiet — Module 4's search, not the SOS screen.
///
/// ⚠️ `gps_loss`, with an **e**. The app matched `gps_lost` until 2026-09-06,
/// which is a string the backend has not written since the alert-type
/// unification in `3fe7896` (Neon holds 44 `gps_loss` against 3 `gps_lost`,
/// none newer than July). `gps_lost` is still the name of an internal dict key
/// in `gps_failure_handling.py`, which is where the wrong spelling came from —
/// it is not, and never was, an `alert_type`.
const missingAlertType = 'gps_loss';

/// The alert currently on screen, so a push arriving while its own screen is
/// already open does not stack a second copy of it.
int? _openAlertId;

/// Opens the right full-screen alert for an FCM payload, or returns false so
/// the caller can fall back to something less interruptive.
///
/// Built from the push data alone wherever possible. The screen it opens polls
/// every 8 s and replaces this with the real row within seconds, so waiting on
/// a network round trip before showing anything would only make an emergency
/// slower — which was the original complaint.
Future<bool> openAlertFromPush(
  GlobalKey<NavigatorState> navigatorKey,
  Map<String, dynamic> data,
) async {
  // A patient device never registers an FCM token (`registerDeviceToken()` is
  // called only on the caregiver paths), so this should not fire there — but
  // if a phone is ever shared between roles, opening a caregiver's alert
  // screen on a patient's app would be worse than showing nothing.
  if (!CaregiverSession.instance.isSignedIn) return false;

  final alertType = data['alert_type'] as String?;
  if (alertType == null) return false;

  final isUrgent = urgentAlertTypes.contains(alertType);
  final isMissing = alertType == missingAlertType;
  if (!isUrgent && !isMissing) return false;

  // FCM data values are always strings — `notification.py` stringifies every
  // one of them, including the numbers.
  final patientId = int.tryParse(data['patient_id'] as String? ?? '');
  final alertId = int.tryParse(data['alert_id'] as String? ?? '');
  if (patientId == null || alertId == null) return false;

  if (_openAlertId == alertId) return true;

  final alert = <String, dynamic>{
    'id': alertId,
    'alert_type': alertType,
    'severity': data['severity'],
    'message': data['message'],
    'latitude': double.tryParse(data['latitude'] as String? ?? ''),
    'longitude': double.tryParse(data['longitude'] as String? ?? ''),
    'resolved': false,
    'claimed_by': null,
    'claimed_by_name': null,
  };

  final patientName = await _patientName(patientId);

  final navigator = navigatorKey.currentState;
  if (navigator == null) return false;

  _openAlertId = alertId;
  try {
    await navigator.push(
      MaterialPageRoute(
        builder: (context) => isMissing
            ? MissingPatientScreen(
                patient: {'id': patientId, 'name': patientName},
                alert: alert,
              )
            : SosAlertScreen(
                patientId: patientId,
                patientName: patientName,
                alert: alert,
              ),
      ),
    );
  } finally {
    _openAlertId = null;
  }
  return true;
}

/// The patient's name, or a placeholder.
///
/// Deliberately never blocks the alert: a caregiver who is told *something* is
/// wrong and has to read the name off the next screen is far better served
/// than one shown nothing because a name lookup timed out.
Future<String> _patientName(int patientId) async {
  try {
    final res = await apiGet('/api/patients/$patientId');
    if (res.statusCode == 200) {
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final name = body['name'] as String?;
      if (name != null && name.isNotEmpty) return name;
    }
  } catch (_) {
    // Falls through to the placeholder.
  }
  return 'Your patient';
}
