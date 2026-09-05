import 'package:geolocator/geolocator.dart';

import 'api_client.dart';
import 'caregiver_session.dart';

/// Reports where this caregiver is, so the patient's SOS screen can rank them.
///
/// `GET /api/patients/{id}/caregivers` has returned `distance_m`,
/// `location_age_s` and `usable` since 2026-08-28, and its sort puts freshness
/// above distance. Nothing in the app had ever written the column those three
/// are computed from, so **every caregiver came back with all three null** and
/// the "nearest caregiver first" ranking silently collapsed to "whoever was
/// registered first" — no error, no empty list, just a confident order built
/// out of nothing. The distance shown beside each name on the patient's SOS
/// screen was blank for the same reason.
///
/// Three limits, all deliberate:
///
/// * **Foreground only — this never asks for `locationAlways`.** The patient's
///   reporter does, because a patient who wanders with the screen off is the
///   entire product. A caregiver is not the person being monitored, and
///   tracking one around the clock buys a ranking refresh nobody asked for.
///   What gets stored is "where they were when they last had the app open",
///   which is exactly what the 1800 s staleness cut-off already assumes.
/// * **Medium accuracy.** This answers "who is nearest", decided in hundreds
///   of metres. A high-accuracy fix would cost battery to sharpen a number
///   that is rounded into a sort order anyway.
/// * **No history.** `PUT .../location` overwrites one row and keeps no trail
///   — a trail would be surveillance of a family member who is not the
///   patient, with no feature behind it.
///
/// Never throws and never blocks a caller. A caregiver who denies location is
/// demoted in the ranking, never dropped from it — an SOS screen that answers
/// "nobody" while somebody is in trouble is worse than one in the wrong order.
Future<void> reportCaregiverLocation() async {
  final caregiverId = CaregiverSession.instance.caregiverId;
  if (caregiverId == null) return;

  try {
    if (!await Geolocator.isLocationServiceEnabled()) return;

    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      return;
    }

    final position = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.medium,
      ),
    );

    // The server stamps `location_updated_at` itself and ignores anything the
    // body might say about time — a wrong phone clock must not be able to make
    // a stale position look fresh enough to win the top tier of the ranking.
    await apiPut('/api/caregivers/$caregiverId/location', body: {
      'latitude': position.latitude,
      'longitude': position.longitude,
    });
  } catch (_) {
    // Location off, permission refused, no fix, or the request failed. All of
    // them mean the same thing to the ranking — this caregiver has no usable
    // position — and it is already the state the endpoint models with null.
  }
}
