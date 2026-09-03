import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'caregiver_homepage_screen.dart';
import 'caregiver_register_screen.dart';
import '../../services/api_client.dart';
import '../../services/device_token_service.dart';
import '../../services/caregiver_session.dart';
class PasswordTextField extends StatelessWidget {
  final TextEditingController controller;
  const PasswordTextField({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: TextField(
        controller: controller,
        obscureText: true,
        decoration: const InputDecoration(
          border: OutlineInputBorder(),
          labelText: 'Password',
          hintText: 'Enter your password',
        ),
      ),
    );
  }
}

class EmailTextField extends StatelessWidget {
  final TextEditingController controller;
  const EmailTextField({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: TextField(
        controller: controller,
        keyboardType: TextInputType.emailAddress,
        decoration: InputDecoration(
          border: OutlineInputBorder(),
          labelText: 'Email',
          hintText: 'Enter your Email',
        ),
      ),
    );
  }
}

class RememberMeCheckBox extends StatefulWidget {
  const RememberMeCheckBox({super.key});

  @override
  State<RememberMeCheckBox> createState() => _RememberMeCheckBoxState();
}

class _RememberMeCheckBoxState extends State<RememberMeCheckBox> {
  bool _isChecked = false;

  @override
  Widget build(BuildContext context) {
    return Checkbox(
      value: _isChecked,
      onChanged: (bool? value) {
        setState(() {
          _isChecked = value ?? false;
        });
      });
  }
}


class CaregiverLoginScreen extends StatefulWidget {
  const CaregiverLoginScreen({super.key});

  @override
  State<CaregiverLoginScreen> createState() => _CaregiverLoginScreenState();
}

class _CaregiverLoginScreenState extends State<CaregiverLoginScreen> {
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  String? _errorMessage;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _handleLogin() async {
    try {
      await FirebaseAuth.instance.signInWithEmailAndPassword(
        email: _emailController.text.trim(),
        password: _passwordController.text,
      );
    } on FirebaseAuthException catch (_) {
      if (!mounted) return;
      setState(() {
        _errorMessage = "Wrong email or password.";
      });
      return;
    }

    final res = await apiGet('/api/me');

    if (res.statusCode == 403) {
      if (!mounted) return;
      setState(() => _errorMessage = 'บัญชีนี้ยังไม่ได้ลงทะเบียนในระบบ');
      return;
    }
    if (res.statusCode != 200) {
      if (!mounted) return;
      setState(() => _errorMessage = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้');
      return;
    }

    final me = jsonDecode(res.body) as Map<String, dynamic>;

    if (me['role'] != 'caregiver') {
      if (!mounted) return;
      setState(() => _errorMessage = 'บัญชีนี้เป็นของผู้ป่วย ไม่ใช่ผู้ดูแล');
      return;
    }

    await CaregiverSession.instance.save(
      caregiverId: me['id'] as int,
      caregiverName: me['name'] as String,
    );
    await registerDeviceToken();

    if (!mounted) return;
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => CaregiverHomePageScreen(
          caregiverName: me['name'] as String,
        ),
      ),
    );
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
                      'Caregiver login',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 16),
                    EmailTextField(controller: _emailController),
                    const SizedBox(height: 16),
                    PasswordTextField(controller: _passwordController),
                    if (_errorMessage != null) ...[
                      const SizedBox(height: 8),
                      Text(_errorMessage!, style: const TextStyle(color: Colors.red,fontSize: 14)),
                    ],
                    const SizedBox(height: 5),
                    const FractionallySizedBox(
                      widthFactor: 0.7,
                      child: Row(
                        children: [
                          RememberMeCheckBox(),
                          Text('Remember me'),
                        ],
                      ),
                    ),
                    const SizedBox(height: 5),
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
                    const FractionallySizedBox(
                      widthFactor: 0.7,
                      child: Divider(
                        color: Colors.grey,
                        thickness: 1,
                      ),
                    ),
                    const SizedBox(height: 10),
                    FractionallySizedBox(
                      widthFactor: 0.7,
                      child: GestureDetector(
                        onTap: () {
                          Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (context) => const CaregiverRegistrationScreen(),
                            ),
                          );
                        },
                        child: RichText(
                          textAlign: TextAlign.center,
                          text: const TextSpan(
                            text: "Need an account? ",
                            style: TextStyle(color: Colors.black),
                            children: <TextSpan>[
                              TextSpan(
                                text: 'SIGN UP',
                                style: TextStyle(
                                  color: Colors.black,
                                  decoration: TextDecoration.underline,
                                  decorationColor: Color.fromARGB(255, 0, 0, 0),
                                  decorationStyle: TextDecorationStyle.wavy,
                                ),
                              ),
                            ],
                          ),
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
