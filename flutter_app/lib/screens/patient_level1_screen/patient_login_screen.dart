import 'package:flutter/material.dart';
import 'dart:convert';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;
import 'patient_homepage_screen.dart';
import '../patient_level2_screen/patient_homepage_screen.dart' as level2;
import '../../services/api_client.dart';
import '../../services/session.dart';
import '../../theme/patient_theme.dart';
class PatientLoginScreen extends StatefulWidget {
  const PatientLoginScreen({super.key});

  @override
  State<PatientLoginScreen> createState() => _PatientLoginScreenState();
}

class _PatientLoginScreenState extends State<PatientLoginScreen> {
  final TextEditingController _idController = TextEditingController();
  String? _errorMessage;
  bool _loggingIn = false;

  /// A cold Cloudflare tunnel/backend can 502 or time out on the very first
  /// request after being idle, then succeed a moment later — retrying a
  /// couple times before surfacing "not found" avoids telling the patient
  /// their correct code is wrong just because the server was still waking up.
  Future<http.Response?> _postWithRetry(String path, {Map<String, dynamic>? body, int attempts = 3}) async {
    for (var i = 0; i < attempts; i++) {
      try {
        final res = await apiPost(path, body: body);
        if (res.statusCode == 200 || res.statusCode == 201) return res;
        if (i == attempts - 1) return res;
      } catch (_) {
        if (i == attempts - 1) return null;
      }
      await Future.delayed(Duration(seconds: 1 + i));
    }
    return null;
  }

  Future<void> _handleLogin() async {
    if (_loggingIn) return;
    setState(() {
      _loggingIn = true;
      _errorMessage = null;
    });
    try {
      await _attemptLogin();
    } finally {
      if (mounted) setState(() => _loggingIn = false);
    }
  }

  Future<void> _attemptLogin() async{
    final code = _idController.text.trim();

    final pairResponse = await _postWithRetry('/api/pair', body: {'code': code});

    if (!mounted) return;

    if (pairResponse == null || pairResponse.statusCode != 200){
      setState(() {
        _errorMessage = 'Patient ID not found. Please check and try again';
      });
      return;
    }

    final pairData = jsonDecode(pairResponse.body);
    await FirebaseAuth.instance.signInWithCustomToken(pairData['firebase_custom_token']);

    final severityLevel = pairData['severity_level'] as int?;

    if (severityLevel == null){
      if (!mounted) return;
      setState(() {
        _errorMessage = 'Ask your caregiver to set a severity level first.';
      });
      return;
    }

    final nameResponse = await apiGet('/api/patients/${pairData['patient_id']}');
    if (!mounted) return;

    final name = nameResponse.statusCode == 200
    ? jsonDecode(nameResponse.body)['name'] as String?
    : null;

    if (name == null){
      if (!mounted) return;
      setState(() {
        _errorMessage = 'Patient data not found. Please check and try again.';
      });
      return;
    }

    final isLevel2 = severityLevel == 2;

    await Session.instance.save(
      patientId: pairData['patient_id'] as int,
      patientName: name,
      severityLevel: severityLevel,
    );

    if (!mounted) return;

    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => isLevel2
            ? level2.PatientHomePageScreen(patientName: name)
            : PatientHomePageScreen(patientName: name),
      ),
    );
  }

  @override
  void dispose() {
    _idController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: SafeArea(
        child: Stack(
          children: [
            Column(
              children: [
                // Calm berry-to-white header — the one accent-color area on
                // this screen, fading into the plain white form below it so
                // the input and button (the actual task) stay the highest
                // contrast, lowest-clutter thing on screen.
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.fromLTRB(24, 72, 24, 40),
                  decoration: BoxDecoration(gradient: PatientColors.berryHeaderGradient()),
                  child: Column(
                    children: [
                      const Icon(Icons.favorite, color: Colors.white, size: 48),
                      const SizedBox(height: 12),
                      const Text(
                        'Patient login',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontSize: 26,
                          fontWeight: FontWeight.bold,
                          color: Colors.white,
                        ),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: Center(
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.symmetric(horizontal: 24),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Text(
                            'Ask your caregiver for your pairing code',
                            textAlign: TextAlign.center,
                            style: TextStyle(fontSize: 16, color: PatientColors.charcoal),
                          ),
                          const SizedBox(height: 20),
                          Semantics(
                            textField: true,
                            label: 'Patient ID, enter your pairing code',
                            child: TextField(
                              controller: _idController,
                              keyboardType: TextInputType.text,
                              textCapitalization: TextCapitalization.characters,
                              style: const TextStyle(fontSize: 24),
                              textAlign: TextAlign.center,
                              decoration: InputDecoration(
                                border: OutlineInputBorder(
                                  borderRadius: BorderRadius.circular(12),
                                ),
                                focusedBorder: OutlineInputBorder(
                                  borderRadius: BorderRadius.circular(12),
                                  borderSide: const BorderSide(color: PatientColors.berry, width: 2),
                                ),
                                labelText: 'Patient ID',
                                hintText: 'Pairing code',
                                contentPadding: const EdgeInsets.symmetric(vertical: 18, horizontal: 16),
                              ),
                            ),
                          ),
                          if (_errorMessage != null) ...[
                            const SizedBox(height: 12),
                            Text(
                              _errorMessage!,
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                color: Colors.red,
                                fontSize: 16,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ],
                          const SizedBox(height: 24),
                          SizedBox(
                            width: double.infinity,
                            child: ElevatedButton(
                              onPressed: _loggingIn ? null : _handleLogin,
                              style: ElevatedButton.styleFrom(
                                backgroundColor: PatientColors.berry,
                                minimumSize: const Size(0, 60),
                                shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(14),
                                ),
                              ),
                              child: _loggingIn
                                  ? const SizedBox(
                                      width: 24,
                                      height: 24,
                                      child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2.5),
                                    )
                                  : const Text(
                                      'Login',
                                      style: TextStyle(
                                        color: Colors.white,
                                        fontSize: 22,
                                        fontWeight: FontWeight.w700,
                                      ),
                                    ),
                            ),
                          ),
                          const SizedBox(height: 15),
                        ],
                      ),
                    ),
                  ),
                ),
              ],
            ),
            const Positioned(
              top: 16,
              left: 16,
              child: PatientBackButton(),
            ),
          ],
        ),
      ),
    );
  }
}
