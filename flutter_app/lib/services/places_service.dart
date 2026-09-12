import 'dart:convert';
import 'package:http/http.dart' as http;
import 'google_api_key.dart';

class PlacePrediction {
  final String description;
  final String placeId;

  PlacePrediction({required this.description, required this.placeId});
}

String _apiKey() => googleWebServicesKey()!;

// Without this a stalled request never comes back, and the caller has no way to
// tell that from "still loading" — on the patient's search results that meant a
// tapped place that simply never opened.
const _placesTimeout = Duration(seconds: 10);

Future<List<PlacePrediction>> fetchAutocomplete(String input, String sessionToken) async {
  final url = Uri.parse('https://places.googleapis.com/v1/places:autocomplete');

  final http.Response response;
  try {
    response = await http.post(
      url,
      headers: {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': _apiKey(),
      },
      body: jsonEncode({
        'input': input,
        'sessionToken': sessionToken,
      }),
    ).timeout(_placesTimeout);
  } catch (_) {
    return [];
  }

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

  final http.Response response;
  try {
    response = await http.get(
      url,
      headers: {
        'X-Goog-Api-Key': _apiKey(),
        'X-Goog-FieldMask': 'displayName,location',
      },
    ).timeout(_placesTimeout);
  } catch (_) {
    return null;
  }

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
