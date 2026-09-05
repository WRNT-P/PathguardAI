import 'package:geolocator/geolocator.dart';
import 'api_client.dart';
import 'session.dart';

Future<bool> triggerSOS() async {
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
  });

  return response.statusCode == 201;
}