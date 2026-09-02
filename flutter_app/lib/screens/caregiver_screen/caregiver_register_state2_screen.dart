import 'dart:convert';
import '../../services/device_token_service.dart';
import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import '../../services/api_client.dart';
import 'caregiver_homepage_screen.dart';
import '../../services/caregiver_session.dart';

class PhoneTextField extends StatelessWidget {
  final TextEditingController controller;
  const PhoneTextField({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: TextField(
        controller: controller,
        keyboardType: TextInputType.phone,
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          labelText: 'Phone Number',
          hintText: 'Enter your phone number',
        ),
      ),
    );
  }
}

class NameTextField extends StatelessWidget {
  final TextEditingController controller;
  const NameTextField({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: TextField(
        controller: controller,
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          labelText: 'Name',
          hintText: 'Enter your name',
        ),
      ),
    );
  }
}

class CaregiverRegistrationState2Screen extends StatefulWidget {
  final String email;
  final String password;
  const CaregiverRegistrationState2Screen({
    super.key,
    required this.email,
    required this.password,
  });

  @override
  State<CaregiverRegistrationState2Screen> createState() => _CaregiverRegistrationState2ScreenState();
}

class _CaregiverRegistrationState2ScreenState extends State<CaregiverRegistrationState2Screen> {
  final TextEditingController _phoneController = TextEditingController();
  final TextEditingController _nameController = TextEditingController();
  String? _errorMessage;

  @override
  void dispose() {
    _phoneController.dispose();
    _nameController.dispose();
    super.dispose();
  }

  Future<void> _handleRegister() async {
    UserCredential? credential;
    try {
      credential = await FirebaseAuth.instance.createUserWithEmailAndPassword(
        email: widget.email,
        password: widget.password,
      );

      final response = await apiPost('/api/register', body: {
        'firebase_uid': credential.user!.uid,
        'name': _nameController.text.trim(),
        'role': 'caregiver',
        'phone': _phoneController.text.trim(),
      });

      if (response.statusCode != 201) {
        await credential.user?.delete();
        if (!mounted) return;
        setState(() {
          _errorMessage = 'Could not register, try again.';
        });
        return;
      }

      final data = jsonDecode(response.body);
      await CaregiverSession.instance.save(
        caregiverId: data['id'] as int,
        caregiverName: _nameController.text.trim(),
      );
      await registerDeviceToken();

      if (!mounted) return;

      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (context) => CaregiverHomePageScreen(
            caregiverName: _nameController.text.trim(),
          ),
        ),
      );
    } on FirebaseAuthException catch (_) {
      if (!mounted) return;
      setState(() {
        _errorMessage = 'Could not create account — check your email and password.';
      });
    } catch (_) {
      await credential?.user?.delete();
      if (!mounted) return;
      setState(() {
        _errorMessage = 'Network error — check your connection and try again.';
      });
    }
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
                      'Caregiver sign up',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 16),
                    NameTextField(controller: _nameController),
                    const SizedBox(height: 16),
                    PhoneTextField(controller: _phoneController),
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
                          onPressed: _handleRegister,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.blue,
                            minimumSize: const Size(0, 48),
                          ),
                          child: const Text('Sign up', style: TextStyle(color: Colors.white)),
                        ),
                      ),
                    ),
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
