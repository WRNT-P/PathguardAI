import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'firebase_options.dart';
import 'services/session.dart';
import 'services/caregiver_session.dart';
import 'screens/login_screen.dart';
import 'screens/patient_level1_screen/patient_homepage_screen.dart';
import 'screens/patient_level2_screen/patient_homepage_screen.dart' as level2;
import 'screens/caregiver_screen/caregiver_homepage_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  FlutterForegroundTask.initCommunicationPort();
  await dotenv.load(fileName: ".env");
  await Firebase.initializeApp(options: DefaultFirebaseOptions.currentPlatform);
  await Session.instance.load();
  await CaregiverSession.instance.load();
  runApp(const MyApp());
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
      home: home,
    );
  }
}
