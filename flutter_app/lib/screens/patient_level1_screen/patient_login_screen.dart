import 'package:flutter/material.dart';
import 'patient_homepage_screen.dart';
import '../patient_level2_screen/patient_homepage_screen.dart' as level2;
import '../../services/patient_directory.dart';

class PatientLoginScreen extends StatefulWidget {
  const PatientLoginScreen({super.key});

  @override
  State<PatientLoginScreen> createState() => _PatientLoginScreenState();
}

class _PatientLoginScreenState extends State<PatientLoginScreen> {
  final TextEditingController _idController = TextEditingController();
  String? _errorMessage;

  void _handleLogin() {
    final id = _idController.text.trim();
    final patient = PatientDirectory.instance.getPatientById(id);

    if (patient == null) {
      setState(() {
        _errorMessage = 'Patient ID not found. Please check and try again.';
      });
      return;
    }

    // Mock-stage routing: reads the level straight from the local
    // PatientDirectory entry (state == '2 : Memory Loss-Severe' -> Level 2).
    // Once the backend ships severity_level in the /api/pair response, this
    // should read from there instead — the routing logic itself won't change.
    final state = patient['state'] as String?;
    final isLevel2 = state != null && state.startsWith('2');

    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => isLevel2
            ? level2.PatientHomePageScreen(patientName: patient['name'])
            : PatientHomePageScreen(patientName: patient['name']),
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
                          onPressed: _handleLogin,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.blue,
                            minimumSize: const Size(0, 48),
                          ),
                          child: const Text(
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
