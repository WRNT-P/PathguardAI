import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:flutter_dotenv/flutter_dotenv.dart';
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

Future<RouteResult?> fetchRoute(LatLng origin, LatLng destination) async {
  final apiKey = Platform.isIOS
      ? dotenv.env['IOS_GOOGLE_MAPS_API_KEY']
      : dotenv.env['ANDROID_GOOGLE_MAPS_API_KEY'];

  final url = Uri.parse(
    'https://maps.googleapis.com/maps/api/directions/json'
    '?origin=${origin.latitude},${origin.longitude}'
    '&destination=${destination.latitude},${destination.longitude}'
    '&mode=walking'
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
  if (response.statusCode != 200) return null;

  final data = jsonDecode(response.body);
  if (data['status'] != 'OK') return null;

  final route = data['routes'][0];
  final overviewPoints = route['overview_polyline']['points'] as String;
  final points = _decodePolyline(overviewPoints);

  final legSteps = route['legs'][0]['steps'] as List<dynamic>;
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
      return 'Turn left';
    case 'turn-right':
    case 'turn-slight-right':
    case 'turn-sharp-right':
    case 'ramp-right':
    case 'fork-right':
      return 'Turn right';
    case 'uturn-left':
    case 'uturn-right':
      return 'Turn around';
    case 'roundabout-left':
    case 'roundabout-right':
      return 'Go through the roundabout';
    case 'merge':
    case 'straight':
    default:
      return 'Go straight ahead';
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