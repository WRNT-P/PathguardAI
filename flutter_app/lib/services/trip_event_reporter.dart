import 'api_client.dart';
import 'session.dart';

/// Reports a trip lifecycle event (started/arrived/off_route) for the
/// signed-in patient — see `backend/app/api/trip_events.py`.
///
/// Fire-and-forget, same policy as the rest of a navigation screen's
/// background calls (SOS lookups, the active-trip heartbeat): a caregiver
/// missing one notification is far better than a walk stalling on a dropped
/// connection.
Future<void> reportTripEvent(
  String event, {
  String? destinationName,
  double? latitude,
  double? longitude,
}) async {
  final patientId = Session.instance.patientId;
  if (patientId == null) return;
  try {
    await apiPost('/api/patients/$patientId/trip-events', body: {
      'event': event,
      if (destinationName != null) 'destination_name': destinationName,
      if (latitude != null) 'latitude': latitude,
      if (longitude != null) 'longitude': longitude,
    });
  } catch (_) {
    // Best-effort — see module doc.
  }
}
