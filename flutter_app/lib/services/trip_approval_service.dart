import 'dart:convert';
import 'api_client.dart';
import 'session.dart';
import 'trip_request_directory.dart';

/// Requests caregiver approval before a Level 2 patient can travel to [place].
/// Creates a real pending request and waits for a caregiver to decide via
/// TripRequestDirectory — no more auto-approving after a fixed delay.
Future<bool> requestTripApproval({
  required String patientName,
  required Map<String, dynamic> place,
}) async {
  final patientId = Session.instance.patientId;
  if (patientId == null) return false;

  double? confidence;
  int? backendId;

  final response = await apiPost('/api/trip-requests', body: {
    'patient_id': patientId,
    'destination_name': place['name'],
    'latitude': place['lat'],
    'longitude': place['lng'],
  });

  if (response.statusCode == 200 || response.statusCode == 201) {
    final trip = jsonDecode(response.body);
    confidence = (trip['confidence'] as num?)?.toDouble();
    backendId = (trip['id'] as num?)?.toInt();
  }

  final request = await TripRequestDirectory.instance.create(
    patientName: patientName,
    place: place,
    confidence: confidence,
    backendId: backendId,
  );
  return request.decision;
}
