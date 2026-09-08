import 'package:geolocator/geolocator.dart';
import 'api_client.dart';
import 'session.dart';

/// Raise an SOS for the paired patient.
///
/// [destinationName] is the safe place the app is about to walk them to. It
/// goes into the alert the caregiver reads, because the coordinates in that
/// alert start going stale the instant the patient sets off — knowing where
/// they are headed is what lets a caregiver meet them instead of chase them.
///
/// [atHome] marks a press made from the home screen rather than mid-journey.
/// The caregiver app lists those instead of taking over the screen with them.
/// It defaults to false so that forgetting to pass it makes an alert louder
/// than intended, never quieter.
Future<bool> triggerSOS({String? destinationName, bool atHome = false}) async {
  final patientId = Session.instance.patientId;
  if (patientId == null) return false;

  // Waiting for a fresh high-accuracy GPS lock before sending is what made
  // this feel slow — an SOS press needs to reach the caregiver fast far more
  // than it needs pinpoint accuracy. A last-known fix is near-instant and
  // good enough; only fall back to a fresh (capped, medium-accuracy) fix if
  // there's truly nothing cached yet.
  Position? here = await Geolocator.getLastKnownPosition();
  if (here == null) {
    try {
      here = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.medium),
      ).timeout(const Duration(seconds: 3));
    } catch (_) {}
  }

  final response = await apiPost('/api/sos', body: {
    'patient_id': patientId,
    if (here != null) 'latitude': here.latitude,
    if (here != null) 'longitude': here.longitude,
    'destination_name': ?destinationName,
    'at_home': atHome,
  });

  return response.statusCode == 201;
}