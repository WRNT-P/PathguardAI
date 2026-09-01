import 'package:flutter/material.dart';
import 'caregiver_register_state2_screen.dart';

class PasswordTextField extends StatelessWidget {
  final TextEditingController controller;
  final String? errorText;
  const PasswordTextField({super.key, required this.controller, this.errorText});

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: TextField(
        controller: controller,
        obscureText: true,
        decoration: InputDecoration(
          border: OutlineInputBorder(),
          labelText: 'Password',
          hintText: 'Enter your password',
          errorText: errorText,
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

class CaregiverRegistrationScreen extends StatefulWidget {
  const CaregiverRegistrationScreen({super.key});

  @override
  State<CaregiverRegistrationScreen> createState() => _CaregiverRegistrationScreenState();
}

class _CaregiverRegistrationScreenState extends State<CaregiverRegistrationScreen> {
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();

  String? _passwordError;
  String? _errorMessage;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }
  
  void _handleNext() {
    setState(() {
      _passwordError = _passwordController.text.length < 6
        ? 'Password must be at least 6 characters'
        : null;
    });
    if (_passwordError != null) return;

    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => CaregiverRegistrationState2Screen(
          email: _emailController.text.trim(),
          password: _passwordController.text,
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
                      'Caregiver sign up',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 16),
                    EmailTextField(controller: _emailController),
                    const SizedBox(height: 16),
                    PasswordTextField(controller: _passwordController, errorText: _passwordError),
                    const SizedBox(height: 16),
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
                          onPressed: _handleNext,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.blue,
                            minimumSize: const Size(0, 48),
                          ),
                          child: const Text(
                            'Next',
                            style: TextStyle(color: Colors.white),
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
