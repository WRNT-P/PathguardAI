import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:flutter_dotenv/flutter_dotenv.dart';

Future<Map<String, dynamic>?> findNearestSafePlace(double lat, double lng) async {
  final apiKey = Platform.isIOS
    ? dotenv.env['IOS_GOOGLE_MAPS_API_KEY']!
    : dotenv.env['ANDROID_GOOGLE_MAPS_API_KEYS']!;

    final url = Uri.parse('https://places.googleapis.com/v1/places:searchNearby');

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
  );

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