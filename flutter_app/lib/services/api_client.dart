import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:firebase_auth/firebase_auth.dart';

// Without this, a hung network call (Firebase token refresh, a dead tunnel)
// leaves the caller awaiting forever with no error and no way to know why —
// a button just looks broken. 15s is generous for a mobile connection but
// still short enough to surface a real problem quickly.
const _requestTimeout = Duration(seconds: 15);

Future<String?> _getAuthToken() async {
  return FirebaseAuth.instance.currentUser?.getIdToken().timeout(_requestTimeout);
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
  return http
      .get(_buildUri(path, queryParams), headers: await _buildHeaders())
      .timeout(_requestTimeout);
}

Future<http.Response> apiPost(String path, {Map<String, dynamic>? body}) async {
  return http
      .post(
        _buildUri(path),
        headers: await _buildHeaders(),
        body: body != null ? jsonEncode(body) : null,
      )
      .timeout(_requestTimeout);
}

Future<http.Response> apiPatch(String path, {Map<String, dynamic>? body}) async {
  return http
      .patch(
        _buildUri(path),
        headers: await _buildHeaders(),
        body: body != null ? jsonEncode(body) : null,
      )
      .timeout(_requestTimeout);
}

Future<http.Response> apiPut(String path, {Map<String, dynamic>? body}) async {
  return http
      .put(
        _buildUri(path),
        headers: await _buildHeaders(),
        body: body != null ? jsonEncode(body) : null,
      )
      .timeout(_requestTimeout);
}
