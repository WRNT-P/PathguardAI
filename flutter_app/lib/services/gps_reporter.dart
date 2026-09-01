import 'dart:async';
import 'package:geolocator/geolocator.dart';
import 'api_client.dart';
import 'session.dart';

StreamSubscription<Position>? _sub;
DateTime? _lastSent;

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

  _sub = Geolocator.getPositionStream(
    locationSettings: const LocationSettings(
      accuracy: LocationAccuracy.high,
      distanceFilter: 10,
    )
  ).listen(_send);
}

Future<void> stopGpsReporting() async {
  await _sub?.cancel();
  _sub = null;
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
