import 'dart:async';
import 'package:geolocator/geolocator.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'api_client.dart';
import 'session.dart';
import 'gps_task_handler.dart';

StreamSubscription<Position>? _sub;
DateTime? _lastSent;
bool _foregroundTaskInitialized = false;

void _initForegroundTask() {
  if (_foregroundTaskInitialized) return;
  _foregroundTaskInitialized = true;
  FlutterForegroundTask.init(
    androidNotificationOptions: AndroidNotificationOptions(
      channelId: 'pathguard_gps_channel',
      channelName: 'PathGuard location tracking',
      channelDescription: 'Keeps sending your location to your caregiver.',
    ),
    iosNotificationOptions: const IOSNotificationOptions(),
    foregroundTaskOptions: ForegroundTaskOptions(
      eventAction: ForegroundTaskEventAction.repeat(60000),
      autoRunOnBoot: false,
      allowWakeLock: true,
    ),
  );
}

Future<void> startGpsReporting() async {
  if (_sub != null) return;
  if (!Session.instance.isPaired) return;
  if (!await Geolocator.isLocationServiceEnabled()) return;

  var permission = await Geolocator.checkPermission();
  if (permission == LocationPermission.denied) {
    permission = await Geolocator.requestPermission();
  }
  if (permission ==LocationPermission.denied || permission == LocationPermission.deniedForever) {
    return;
  }

  // Foreground location grant only gets us "while in use"; background
  // tracking needs "allow all the time", which Android 10+ requires asking
  // for separately (and routes to a Settings screen, not an in-app dialog).
  await Permission.locationAlways.request();

  _sub = Geolocator.getPositionStream(
    locationSettings: const LocationSettings(
      accuracy: LocationAccuracy.high,
      distanceFilter: 10,
    )
  ).listen(_send);

  _initForegroundTask();
  await FlutterForegroundTask.startService(
    serviceId: 256,
    notificationTitle: 'PathGuard is tracking your location',
    notificationText: 'This keeps your caregiver updated even when the screen is off.',
    callback: startGpsCallback,
  );
}

Future<void> stopGpsReporting() async {
  await _sub?.cancel();
  _sub = null;
  await FlutterForegroundTask.stopService();
}

Future<void> _send(Position p) async {
  final patientId = Session.instance.patientId;
  if (patientId == null) return;

  final now = DateTime.now();
  if (_lastSent != null && now.difference(_lastSent!) < const Duration(seconds: 20)) {
    return;
  }
  _lastSent = now;

  final heading = (p.heading >= 0 && p.heading < 360) ? p.heading: null;

   try {
    await apiPost('/api/gps', body: {
      'patient_id': patientId,
      'latitude': p.latitude,
      'longitude': p.longitude,
      'accuracy': p.accuracy,
      'altitude': p.altitude,
      'speed': p.speed,
      'direction': heading,
      'recorded_at': p.timestamp.toUtc().toIso8601String(),
    });
  } catch (_) {
  }
}
