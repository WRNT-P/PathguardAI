import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'firebase_options.dart';
import 'services/session.dart';
import 'services/caregiver_session.dart';
import 'screens/login_screen.dart';
import 'screens/patient_level1_screen/patient_homepage_screen.dart';
import 'screens/patient_level2_screen/patient_homepage_screen.dart' as level2;
import 'screens/caregiver_screen/caregiver_homepage_screen.dart';
import 'services/device_token_service.dart';
import 'services/alert_navigation.dart';

// Lets the FCM foreground listener show something even though it isn't
// inside any screen's widget tree — there was previously no code at all
// reacting to a push while the app was open, so a caregiver sitting on the
// homepage never learned an alert had arrived until they closed and reopened
// the app (which re-runs the alert-polling check in initState).
final navigatorKey = GlobalKey<NavigatorState>();

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  FlutterForegroundTask.initCommunicationPort();
  await dotenv.load(fileName: ".env");
  await Firebase.initializeApp(options: DefaultFirebaseOptions.currentPlatform);
  await Session.instance.load();
  await CaregiverSession.instance.load();
  await _initPushNotifications();
  // A caregiver already signed in from a previous app launch (persisted
  // session, not a fresh login) would otherwise never register a device
  // token — registerDeviceToken() was only ever called from the login/
  // register success paths.
  if (CaregiverSession.instance.isSignedIn) {
    await registerDeviceToken();
  }
  runApp(const MyApp());
}

/// Android 13+ silently drops every FCM push unless this runtime permission
/// is granted — declaring POST_NOTIFICATIONS in the manifest alone is not
/// enough. Also covers the foreground case: Android only auto-displays a
/// push's system-tray notification while the app is backgrounded/killed, so
/// an alert arriving while a caregiver has the app open needs its own
/// visible signal here.
///
/// That signal used to be a SnackBar for *every* push, whatever it said. The
/// payload was never read: an SOS from a patient with dementia and a routine
/// notice were given the same eight seconds of grey bar at the bottom of the
/// screen, and the full-screen alert that already existed was only reachable
/// by closing the app and opening it again — the one thing nobody does while
/// holding a phone that has just buzzed. Urgent types now open their screen
/// directly and the SnackBar keeps everything else.
Future<void> _initPushNotifications() async {
  await FirebaseMessaging.instance.requestPermission();
  FirebaseMessaging.onMessage.listen((message) async {
    // Try the full-screen route first — it decides for itself whether this
    // payload is one it handles, so the two lists cannot disagree.
    if (await openAlertFromPush(navigatorKey, message.data)) return;

    final context = navigatorKey.currentContext;
    if (context == null || !context.mounted) return;
    final title = message.notification?.title ?? 'PathGuard alert';
    final body = message.notification?.body ?? '';
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        duration: const Duration(seconds: 8),
        content: Text(body.isEmpty ? title : '$title: $body'),
      ),
    );
  });
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    Widget home = const LoginScreen();
    if (Session.instance.isPaired) {
      home = Session.instance.severityLevel == 2
        ? level2.PatientHomePageScreen(patientName:Session.instance.patientName)
        : PatientHomePageScreen(patientName:Session.instance.patientName);
    } else if (CaregiverSession.instance.isSignedIn) {
      home = CaregiverHomePageScreen(caregiverName: CaregiverSession.instance.caregiverName);
    }
    return MaterialApp(
      navigatorKey: navigatorKey,
      home: home,
    );
  }
}
