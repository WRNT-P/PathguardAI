import 'package:flutter/material.dart';
import 'caregiver_register_state2_screen.dart';

class PasswordTextField extends StatefulWidget {
  final TextEditingController controller;
  final String? errorText;
  final String label;
  final String hint;
  const PasswordTextField({
    super.key,
    required this.controller,
    this.errorText,
    this.label = 'รหัสผ่าน',
    this.hint = 'กรอกรหัสผ่าน',
  });

  @override
  State<PasswordTextField> createState() => _PasswordTextFieldState();
}

class _PasswordTextFieldState extends State<PasswordTextField> {
  bool _obscure = true;

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: TextField(
        controller: widget.controller,
        obscureText: _obscure,
        decoration: InputDecoration(
          border: OutlineInputBorder(),
          labelText: widget.label,
          hintText: widget.hint,
          errorText: widget.errorText,
          suffixIcon: IconButton(
            icon: Icon(_obscure ? Icons.visibility_off : Icons.visibility),
            tooltip: _obscure ? 'แสดงรหัสผ่าน' : 'ซ่อนรหัสผ่าน',
            onPressed: () => setState(() => _obscure = !_obscure),
          ),
        ),
      ),
    );
  }
}

class EmailTextField extends StatelessWidget {
  final TextEditingController controller;
  final String? errorText;
  const EmailTextField({super.key, required this.controller, this.errorText});

  @override
  Widget build(BuildContext context) {
    return FractionallySizedBox(
      widthFactor: 0.7,
      child: TextField(
        controller: controller,
        keyboardType: TextInputType.emailAddress,
        decoration: InputDecoration(
          border: OutlineInputBorder(),
          labelText: 'อีเมล',
          hintText: 'กรอกอีเมลของคุณ',
          errorText: errorText,
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
  final TextEditingController _confirmController = TextEditingController();

  String? _emailError;
  String? _passwordError;
  String? _confirmError;
  String? _errorMessage;

  static final _emailPattern = RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$');

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    _confirmController.dispose();
    super.dispose();
  }

  void _handleNext() {
    setState(() {
      _emailError = _emailPattern.hasMatch(_emailController.text.trim())
        ? null
        : 'กรุณากรอกอีเมลให้ถูกต้อง';
      _passwordError = _passwordController.text.length < 6
        ? 'รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร'
        : null;
      _confirmError = _confirmController.text != _passwordController.text
        ? 'รหัสผ่านไม่ตรงกัน'
        : null;
    });
    if (_emailError != null || _passwordError != null || _confirmError != null) {
      return;
    }

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
                      'สมัครสมาชิกผู้ดูแล',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 16),
                    EmailTextField(controller: _emailController, errorText: _emailError),
                    const SizedBox(height: 16),
                    PasswordTextField(controller: _passwordController, errorText: _passwordError),
                    const SizedBox(height: 16),
                    PasswordTextField(
                      controller: _confirmController,
                      errorText: _confirmError,
                      label: 'ยืนยันรหัสผ่าน',
                      hint: 'กรอกรหัสผ่านอีกครั้ง',
                    ),
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
                            'ถัดไป',
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
