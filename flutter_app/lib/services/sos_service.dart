import 'package:geolocator/geolocator.dart';
import 'api_client.dart';
import 'session.dart';

Future<bool> triggerSOS() async {
  final patientId = Session.instance.patientId;
  if (patientId == null) return false;

  Position? here;
  try {
    here = await Geolocator.getCurrentPosition()
      .timeout(const Duration(seconds: 5));
  } catch (_) {}

  final response = await apiPost('/api/sos', body: {
    'patient_id': patientId,
    if (here != null) 'latitude': here.latitude,
    if (here != null) 'longitude': here.longitude,
  });

  return response.statusCode == 201;
}