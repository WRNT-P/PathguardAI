import 'package:flutter/material.dart';
import 'dart:convert';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;
import 'patient_homepage_screen.dart';
import '../patient_level2_screen/patient_homepage_screen.dart' as level2;
import '../../services/api_client.dart';
import '../../services/session.dart';
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
      body: SafeArea(
        child: Stack(
          children: [
            Center(
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text(
                      'Patient login',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 16),
                    FractionallySizedBox(
                      widthFactor: 0.7,
                      child: TextField(
                        controller: _idController,
                        keyboardType: TextInputType.text,
                        textCapitalization: TextCapitalization.characters,
                        style: const TextStyle(fontSize: 20),
                        decoration: const InputDecoration(
                          border: OutlineInputBorder(),
                          labelText: 'Patient ID',
                          hintText: 'Enter your pairing code',
                        ),
                      ),
                    ),
                    if (_errorMessage != null) ...[
                      const SizedBox(height: 8),
                      Text(_errorMessage!, style: const TextStyle(color: Colors.red, fontSize: 14)),
                    ],
                    const SizedBox(height: 16),
                    FractionallySizedBox(
                      widthFactor: 0.7,
                      child: SizedBox(
                        width: double.infinity,
                        child: ElevatedButton(
                          onPressed: _loggingIn ? null : _handleLogin,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.blue,
                            minimumSize: const Size(0, 48),
                          ),
                          child: _loggingIn
                              ? const SizedBox(
                                  width: 20,
                                  height: 20,
                                  child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2.5),
                                )
                              : const Text(
                                  'Login',
                                  style: TextStyle(color: Colors.white),
                                ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 15),
                  ],
                ),
              ),
            ),
            Positioned(
              top: 16,
              left: 16,
              child: Container(
                decoration: BoxDecoration(
                  color: Colors.grey[200],
                  shape: BoxShape.circle,
                ),
                child: IconButton(
                  icon: const Icon(
                    Icons.arrow_back,
                    color: Colors.black,
                    size: 20,
                  ),
                  onPressed: () {
                    Navigator.pop(context);
                  },
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
