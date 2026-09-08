import 'dart:convert';
import 'package:http/http.dart' as http;
import 'google_api_key.dart';

Future<Map<String, dynamic>?> findNearestSafePlace(double lat, double lng) async {
  final apiKey = googleWebServicesKey()!;

    final url = Uri.parse('https://places.googleapis.com/v1/places:searchNearby');

    // Was unbounded — a slow or dropped connection here could hang the SOS
    // flow indefinitely with no way for the caller's own timeout to help,
    // since this request had none of its own.
    final response = await http.post(
      url,
      headers: {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': apiKey,
        'X-Goog-FieldMask': 'places.displayName,places.location',
      },
      body: jsonEncode({
        'includedTypes': ['police', 'hospital'],
        'maxResultCount': 5,
        'rankPreference': 'DISTANCE',
        'locationRestriction': {
        'circle': {
          'center': {'latitude': lat, 'longitude': lng},
          'radius': 3000.0,
        },
      },
    })
  ).timeout(const Duration(seconds: 8));

  if (response.statusCode != 200) return null;

  final data = jsonDecode(response.body);
  final places = data['places'] as List?;
  if (places == null || places.isEmpty) return null;

  final nearest = places.first;
  return {
    'name': nearest['displayName']['text'] as String,
    'lat': (nearest['location']['latitude'] as num).toDouble(),
    'lng': (nearest['location']['longitude'] as num).toDouble(),
  };
}