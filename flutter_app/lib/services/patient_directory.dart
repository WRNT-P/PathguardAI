import 'dart:math';

class PatientDirectory {
  PatientDirectory._();
  static final PatientDirectory instance = PatientDirectory._();

  final Map<String, Map<String, dynamic>> _patients = {};

  String addPatient(Map<String, dynamic> data) {
    final id = _generatedId();
    _patients[id] = data;
    return id;
  }

  Map <String, dynamic>? getPatientById(String id) {
    return _patients[id];
  }

  String _generatedId() {
    final random = Random();
    String id;
    do {
      id = List.generate(6, (_) => random.nextInt(10)).join();
    } while (_patients.containsKey(id));
    return id;
  }
}