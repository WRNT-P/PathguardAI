import 'dart:io';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'api_client.dart';
import 'caregiver_session.dart';

Future<void> registerDeviceToken() async {
  final caregiverId = CaregiverSession.instance.caregiverId;
  if (caregiverId == null) return;

  final token = await FirebaseMessaging.instance.getToken();
  if (token == null) return;

  await _sendToken(caregiverId, token);

  FirebaseMessaging.instance.onTokenRefresh.listen((newToken) {
    final id = CaregiverSession.instance.caregiverId;
    if (id != null) _sendToken(id, newToken);
  });
}

Future<void> _sendToken(int caregiverId, String token) async {
  try {
    await apiPost('/api/devices/token', body: {
      'user_id': caregiverId,
      'token': token,
      'platform': Platform.isIOS ? 'ios' : 'android',
    });
  } catch (_) {}
}