import 'package:shared_preferences/shared_preferences.dart';

class CaregiverSession {
  CaregiverSession._();
  static final CaregiverSession instance = CaregiverSession._();

  int? caregiverId;
  String? caregiverName;

  bool get isSignedIn => caregiverId != null;

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    caregiverId = prefs.getInt('caregiver_id');
    caregiverName = prefs.getString('caregiver_name');
  }

  Future<void> save({
    required int caregiverId,
    required String caregiverName,
  }) async {
    this.caregiverId = caregiverId;
    this.caregiverName = caregiverName;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('caregiver_id', caregiverId);
    await prefs.setString('caregiver_name', caregiverName);
  }

  Future<void> clear() async {
    caregiverId = null;
    caregiverName = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('caregiver_id');
    await prefs.remove('caregiver_name');
  }
}