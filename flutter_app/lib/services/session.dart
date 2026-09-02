import 'package:shared_preferences/shared_preferences.dart';

class Session {
  Session._();
  static final Session instance = Session._();

  int? patientId;
  String? patientName;
  int? severityLevel;

  bool get isPaired => patientId != null;

  Future<void> load() async{
    final prefs = await SharedPreferences.getInstance();
    patientId = prefs.getInt('patient_id');
    patientName = prefs.getString('patient_name');
    severityLevel = prefs.getInt('severity_level');
  }

  Future<void> save({
    required int patientId,
    required String patientName,
    required int severityLevel,
  }) async {
    this.patientId = patientId;
    this.patientName = patientName;
    this.severityLevel = severityLevel;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('patient_id', patientId);
    await prefs.setString('patient_name', patientName);
    await prefs.setInt('severity_level', severityLevel);
  }

  Future<void> clear() async {
    patientId = null;
    patientName = null;
    severityLevel = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('patient_id');
    await prefs.remove('patient_name');
    await prefs.remove('severity_level');
  }
}