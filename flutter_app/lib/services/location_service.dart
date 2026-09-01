import 'dart:io';

class ParsedLocation {
  final double latitude;
  final double longitude;
  ParsedLocation(this.latitude, this.longitude);
}

// Matches a "lat,lng" pair like "13.7563,100.5018" — the format Google Maps
// URLs embed either after "@" (map view links) or after "q=" (pin links).
final RegExp _coordinatePattern = RegExp(r'(-?\d+\.\d+),(-?\d+\.\d+)');

ParsedLocation? _extractCoordinates(String text) {
  final match = _coordinatePattern.firstMatch(text);
  if (match == null) return null;

  final lat = double.parse(match.group(1)!);
  final lng = double.parse(match.group(2)!);

  final isValidLat = lat >= -90 && lat <= 90;
  final isValidLng = lng >= -180 && lng <= 180;
  if (!isValidLat || !isValidLng) return null;

  return ParsedLocation(lat, lng);
}

Future<ParsedLocation?> parseGoogleMapsLink(String input) async {
  final trimmed = input.trim();
  if (trimmed.isEmpty) return null;

  // Step 1: most Google Maps links (desktop share, "@lat,lng,zoom" view
  // links, "?q=lat,lng" pin links) already contain the coordinates as text.
  final direct = _extractCoordinates(trimmed);
  if (direct != null) return direct;

  // Step 2: the mobile "Share" button usually produces a shortened
  // maps.app.goo.gl link instead, which has no coordinates in it — only
  // the destination it redirects to does. Follow the redirect to get there.
  final isShortLink = trimmed.contains('goo.gl') || trimmed.contains('maps.app');
  if (!isShortLink) return null;

  try {
    final resolvedUrl = await _resolveRedirect(trimmed);
    return _extractCoordinates(resolvedUrl);
  } catch (_) {
    // Network error, invalid URL, etc. — treat as "couldn't parse".
    return null;
  }
}

// Short links can redirect more than once (e.g. through a consent/interstitial
// page) before landing on the real maps URL. HttpClient follows all of them
// automatically and records the chain in `response.redirects` — the last
// entry is the final destination.
Future<String> _resolveRedirect(String url) async {
  final client = HttpClient();
  try {
    final request = await client.getUrl(Uri.parse(url));
    final response = await request.close();
    if (response.redirects.isNotEmpty) {
      return response.redirects.last.location.toString();
    }
    return url;
  } finally {
    client.close();
  }
}

