import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:flutter_dotenv/flutter_dotenv.dart';

class PlacePrediction {
  final String description;
  final String placeId;

  PlacePrediction({required this.description, required this.placeId});
}

String _apiKey() {
  return Platform.isIOS
      ? dotenv.env['IOS_GOOGLE_MAPS_API_KEY']!
      : dotenv.env['ANDROID_GOOGLE_MAPS_API_KEY']!;
}

Future<List<PlacePrediction>> fetchAutocomplete(String input, String sessionToken) async {
  final url = Uri.parse('https://places.googleapis.com/v1/places:autocomplete');

  final response = await http.post(
    url,
    headers: {
      'Content-Type': 'application/json',
      'X-Goog-Api-Key': _apiKey(),
    },
    body: jsonEncode({
      'input': input,
      'sessionToken': sessionToken,
    }),
  );

  // TEMP DEBUG — remove once search is confirmed working
  print('Autocomplete statusCode: ${response.statusCode}');
  print('Autocomplete body: ${response.body}');

  if (response.statusCode != 200) return [];

  final data = jsonDecode(response.body);
  final suggestions = data['suggestions'] as List<dynamic>?;
  if (suggestions == null) return [];

  return suggestions.map((s) {
    final prediction = s['placePrediction'];
    return PlacePrediction(
      description: prediction['text']['text'] as String,
      placeId: prediction['placeId'] as String,
    );
  }).toList();
}

Future<Map<String, dynamic>?> fetchPlaceDetails(String placeId, String sessionToken) async {
  final url = Uri.parse('https://places.googleapis.com/v1/places/$placeId?sessionToken=$sessionToken');

  final response = await http.get(
    url,
    headers: {
      'X-Goog-Api-Key': _apiKey(),
      'X-Goog-FieldMask': 'displayName,location',
    },
  );

  if (response.statusCode != 200) return null;

  final data = jsonDecode(response.body);
  final location = data['location'];
  if (location == null) return null;

  return {
    'name': data['displayName']['text'] as String,
    'lat': (location['latitude'] as num).toDouble(),
    'lng': (location['longitude'] as num).toDouble(),
  };
}
