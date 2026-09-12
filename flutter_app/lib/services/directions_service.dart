import 'dart:convert';
import 'package:http/http.dart' as http;
import 'google_api_key.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';

/// One leg of a walking route — mirrors what Google's Directions API already
/// returns per-step, just with the HTML stripped out of the instruction text.
class RouteStep {
  final String instruction;
  final double distanceMeters;
  final LatLng endLocation;

  RouteStep({
    required this.instruction,
    required this.distanceMeters,
    required this.endLocation,
  });
}

class RouteResult {
  final List<LatLng> points;
  final List<RouteStep> steps;

  RouteResult({required this.points, required this.steps});
}

/// [mode] is Google's travel mode. Patients walk, which is why that is the
/// default; a caregiver answering an SOS is usually getting in a car.
Future<RouteResult?> fetchRoute(
  LatLng origin,
  LatLng destination, {
  String mode = 'walking',
}) async {
  final apiKey = googleWebServicesKey();

  final url = Uri.parse(
    'https://maps.googleapis.com/maps/api/directions/json'
    '?origin=${origin.latitude},${origin.longitude}'
    '&destination=${destination.latitude},${destination.longitude}'
    '&mode=$mode'
    '&key=$apiKey',
  );

  http.Response response;
  try {
    // Was unbounded — a slow/dropped connection here left the navigation
    // screen with no route and no error, since the caller's own timeout
    // has nothing to catch if this call never returns at all.
    response = await http.get(url).timeout(const Duration(seconds: 8));
  } catch (_) {
    return null;
  }
  if (response.statusCode != 200) {
    print('directions statusCode: ${response.statusCode}');
    return null;
  }

  final data = jsonDecode(response.body);
  if (data['status'] != 'OK') {
    print('directions status : ${data['status']}, error: ${data['error_message']}');
    return null;
  }

  final route = data['routes'][0];
  final legSteps = route['legs'][0]['steps'] as List<dynamic>;

  // Stitched from the per-step polylines, not `overview_polyline`. The
  // overview is deliberately simplified for drawing a whole country on one
  // screen; at the zoom a walking patient sees, its straightened corners sit
  // off the road — and route_deviation measures against this same line, so a
  // smoothed one reports a patient as off-route while they walk down the
  // middle of the pavement.
  final points = <LatLng>[];
  for (final step in legSteps) {
    final encoded = step['polyline']?['points'] as String?;
    if (encoded == null) continue;
    final leg = _decodePolyline(encoded);
    // Each step repeats the previous step's last point.
    if (points.isNotEmpty && leg.isNotEmpty && points.last == leg.first) {
      points.addAll(leg.skip(1));
    } else {
      points.addAll(leg);
    }
  }
  if (points.isEmpty) {
    points.addAll(_decodePolyline(route['overview_polyline']['points'] as String));
  }

  final steps = legSteps.map((step) {
    final instruction = _maneuverText(step['maneuver'] as String?);
    final distanceMeters = (step['distance']['value'] as num).toDouble();
    final endLoc = step['end_location'];
    return RouteStep(
      instruction: instruction,
      distanceMeters: distanceMeters,
      endLocation: LatLng(endLoc['lat'], endLoc['lng']),
    );
  }).toList();

  return RouteResult(points: points, steps: steps);
}

/// Turns Google's `maneuver` category into a short, plain instruction —
/// deliberately dropping street/road names. Google's free-text instructions
/// (`html_instructions`) sometimes contain raw route codes instead of a
/// readable name (e.g. "Turn left onto นธ.4006") for unnamed roads, which is
/// meaningless or actively confusing to a patient. `maneuver` is missing on
/// simple continuation steps — those default to "Go straight ahead".
String _maneuverText(String? maneuver) {
  switch (maneuver) {
    case 'turn-left':
    case 'turn-slight-left':
    case 'turn-sharp-left':
    case 'ramp-left':
    case 'fork-left':
      return 'เลี้ยวซ้าย';
    case 'turn-right':
    case 'turn-slight-right':
    case 'turn-sharp-right':
    case 'ramp-right':
    case 'fork-right':
      return 'เลี้ยวขวา';
    case 'uturn-left':
    case 'uturn-right':
      return 'กลับหลังหัน';
    case 'roundabout-left':
    case 'roundabout-right':
      return 'ผ่านวงเวียน';
    case 'merge':
    case 'straight':
    default:
      return 'เดินตรงไป';
  }
}

List<LatLng> _decodePolyline(String encoded) {
  final points = <LatLng>[];
  int index = 0, len = encoded.length;
  int lat = 0, lng = 0;

  while (index < len) {
    int shift = 0, result = 0;
    int b;
    do {
      b = encoded.codeUnitAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lat += (result & 1) != 0 ? ~(result >> 1) : (result >> 1);

    shift = 0;
    result = 0;
    do {
      b = encoded.codeUnitAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lng += (result & 1) != 0 ? ~(result >> 1) : (result >> 1);

    points.add(LatLng(lat / 1e5, lng / 1e5));
  }
  return points;
}