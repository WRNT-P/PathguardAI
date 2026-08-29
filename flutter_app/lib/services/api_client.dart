import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:flutter_dotenv/flutter_dotenv.dart';

/// Shared client for calling PathGuard's own backend (not third-party APIs
/// like Google Maps — those call `http` directly). Every request goes
/// through here so the Authorization header and base URL are set in exactly
/// one place, matching the API contract's rules:
///  - every endpoint must receive `Authorization: Bearer <Firebase ID token>`
///    from day one, even while the backend ignores it (AUTH_ENABLED=false)
///  - the base URL must never be hardcoded — it's a Cloudflare Tunnel URL
///    that changes on every backend restart, so it comes from .env instead

/// Placeholder until Firebase Auth is wired in — swap this body for
/// `FirebaseAuth.instance.currentUser?.getIdToken()` once it's set up.
/// Must be re-fetched on every call, never cached, since ID tokens expire.
Future<String?> _getAuthToken() async {
  return null;
}

Uri _buildUri(String path, [Map<String, dynamic>? queryParams]) {
  final baseUrl = dotenv.env['BACKEND_BASE_URL'] ?? '';
  return Uri.parse('$baseUrl$path').replace(
    queryParameters: queryParams?.map((key, value) => MapEntry(key, value.toString())),
  );
}

Future<Map<String, String>> _buildHeaders() async {
  final token = await _getAuthToken();
  return {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer ${token ?? ''}',
  };
}

Future<http.Response> apiGet(String path, {Map<String, dynamic>? queryParams}) async {
  return http.get(_buildUri(path, queryParams), headers: await _buildHeaders());
}

Future<http.Response> apiPost(String path, {Map<String, dynamic>? body}) async {
  return http.post(
    _buildUri(path),
    headers: await _buildHeaders(),
    body: body != null ? jsonEncode(body) : null,
  );
}

Future<http.Response> apiPatch(String path, {Map<String, dynamic>? body}) async {
  return http.patch(
    _buildUri(path),
    headers: await _buildHeaders(),
    body: body != null ? jsonEncode(body) : null,
  );
}

Future<http.Response> apiPut(String path, {Map<String, dynamic>? body}) async {
  return http.put(
    _buildUri(path),
    headers: await _buildHeaders(),
    body: body != null ? jsonEncode(body) : null,
  );
}
