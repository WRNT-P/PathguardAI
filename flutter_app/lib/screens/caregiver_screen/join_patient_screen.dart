import 'dart:convert';

import 'package:flutter/material.dart';

import '../../services/api_client.dart';
import '../../services/caregiver_session.dart';

/// The other end of [InviteCaregiverScreen]: type the code, gain access.
///
/// This mints no token and creates no account, unlike device pairing. Whoever
/// is here already registered and signed in — the only thing missing is the
/// row saying which patient they may see.
class JoinPatientScreen extends StatefulWidget {
  const JoinPatientScreen({super.key});

  @override
  State<JoinPatientScreen> createState() => _JoinPatientScreenState();
}

class _JoinPatientScreenState extends State<JoinPatientScreen> {
  final _controller = TextEditingController();
  bool _sending = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _redeem() async {
    final code = _controller.text.trim();
    if (code.isEmpty) {
      setState(() => _error = 'Enter the code the other caregiver gave you.');
      return;
    }

    setState(() {
      _sending = true;
      _error = null;
    });

    try {
      final response = await apiPost('/api/caregivers/redeem-invite', body: {
        'code': code,
        // Required while AUTH_ENABLED is off. With auth on the server takes the
        // caregiver from the token and ignores this, so sending it always is
        // safe and means the screen does not have to know which mode it is in.
        'caregiver_id': CaregiverSession.instance.caregiverId,
      });
      if (!mounted) return;

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _showJoined(
          data['patient_name'] as String,
          data['already_linked'] as bool,
        );
      } else {
        setState(() => _error = _messageFor(response.statusCode));
      }
    } catch (_) {
      if (mounted) {
        setState(() => _error =
            'Could not reach the server. Check your connection and try again.');
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  /// The server answers all three bad-code cases with one 404 on purpose —
  /// telling an outsider that a code exists but has expired confirms that it
  /// exists — so this cannot say which of the three it was, and does not
  /// pretend to. What it can do is name the one action that fixes all three.
  String _messageFor(int status) {
    switch (status) {
      case 404:
        return 'That code did not work. It may be mistyped, already used, or '
            'older than 24 hours. Ask for a new one.';
      case 422:
        return 'This account cannot join a patient. Sign in as a caregiver and '
            'try again.';
      case 401:
        return 'Your sign-in has expired. Sign in again and retry.';
      default:
        return 'Could not join (error $status). Please try again.';
    }
  }

  void _showJoined(String patientName, bool alreadyLinked) {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AlertDialog(
        icon: const Icon(Icons.check_circle, color: Colors.green, size: 56),
        title: Text(alreadyLinked ? 'Already yours' : 'You can now see $patientName'),
        content: Text(
          alreadyLinked
              // Not an error. The code was spent either way — one that stayed
              // live because the holder was already linked is a code that can
              // be passed on to somebody who is not.
              ? 'You already had access to $patientName. Nothing changed, and '
                  'the code has now been used up.'
              : 'You will get their alerts and can see where they are.',
          style: const TextStyle(fontSize: 16),
        ),
        actions: [
          TextButton(
            onPressed: () {
              Navigator.pop(dialogContext);
              Navigator.pop(context, true); // tell the homepage to reload
            },
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Join a patient')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Enter the invite code',
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            const Text(
              'A caregiver who already looks after the patient can create one '
              'for you from their patient list.',
              style: TextStyle(fontSize: 16, color: Colors.black87),
            ),
            const SizedBox(height: 24),
            TextField(
              controller: _controller,
              autofocus: true,
              textCapitalization: TextCapitalization.characters,
              textAlign: TextAlign.center,
              style: const TextStyle(
                fontSize: 30,
                fontWeight: FontWeight.bold,
                letterSpacing: 4,
              ),
              decoration: const InputDecoration(
                hintText: 'ABCD-1234',
                border: OutlineInputBorder(),
              ),
              onSubmitted: (_) => _sending ? null : _redeem(),
            ),
            const SizedBox(height: 20),
            ElevatedButton(
              onPressed: _sending ? null : _redeem,
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.blue,
                foregroundColor: Colors.white,
                minimumSize: const Size(0, 52),
              ),
              child: _sending
                  ? const SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.white),
                    )
                  : const Text('Join', style: TextStyle(fontSize: 18)),
            ),
            if (_error != null) ...[
              const SizedBox(height: 20),
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: Colors.red.shade50,
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: Colors.red.shade200),
                ),
                child: Row(
                  children: [
                    Icon(Icons.error_outline, color: Colors.red.shade700),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        _error!,
                        style: TextStyle(fontSize: 15, color: Colors.red.shade900),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
