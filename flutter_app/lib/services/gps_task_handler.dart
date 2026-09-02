import 'package:flutter/widgets.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:geolocator/geolocator.dart';
import '../firebase_options.dart';
import 'api_client.dart';
import 'session.dart';

@pragma('vm:entry-point')
void startGpsCallback() {
  FlutterForegroundTask.setTaskHandler(GpsTaskHandler());
}

/// Runs in its own isolate, separate from the main app — survives the app
/// being swiped away from recents, which the main isolate does not.
/// Re-initializes everything it needs (Firebase, Session) from scratch since
/// nothing from the main isolate carries over.
class GpsTaskHandler extends TaskHandler {
  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    WidgetsFlutterBinding.ensureInitialized();
    await Firebase.initializeApp(options: DefaultFirebaseOptions.currentPlatform);
    await Session.instance.load();
  }

  @override
  void onRepeatEvent(DateTime timestamp) async {
    final patientId = Session.instance.patientId;
    if (patientId == null) return;

    Position position;
    try {
      position = await Geolocator.getCurrentPosition();
    } catch (_) {
      return;
    }

    final heading = (position.heading >= 0 && position.heading < 360) ? position.heading : null;

    try {
      await apiPost('/api/gps', body: {
        'patient_id': patientId,
        'latitude': position.latitude,
        'longitude': position.longitude,
        'accuracy': position.accuracy,
        'altitude': position.altitude,
        'speed': position.speed,
        'direction': heading,
        'recorded_at': position.timestamp.toUtc().toIso8601String(),
      });
    } catch (_) {
    }
  }

  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {}
}
