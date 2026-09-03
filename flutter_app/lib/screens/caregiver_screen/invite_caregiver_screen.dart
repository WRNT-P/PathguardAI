import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../services/api_client.dart';

/// Issue a code that lets a second caregiver see this patient.
///
/// Deliberately not the same code space as device pairing. A pairing code
/// claims a patient's *identity* — the phone that redeems one becomes the
/// patient. An invite grants *access to* them. One screen showing both would
/// invite the mistake of reading the wrong code out over the phone, and the
/// wrong one hands somebody a caregiver's view of a dementia patient's live
/// position.
class InviteCaregiverScreen extends StatefulWidget {
  final int patientId;
  final String patientName;

  const InviteCaregiverScreen({
    super.key,
    required this.patientId,
    required this.patientName,
  });

  @override
  State<InviteCaregiverScreen> createState() => _InviteCaregiverScreenState();
}

class _InviteCaregiverScreenState extends State<InviteCaregiverScreen> {
  bool _sending = false;
  String? _code;
  DateTime? _expiresAt;
  String? _error;

  Future<void> _createInvite() async {
    setState(() {
      _sending = true;
      _error = null;
    });

    try {
      final response =
          await apiPost('/api/patients/${widget.patientId}/caregiver-invites');
      if (!mounted) return;

      if (response.statusCode == 201) {
        final data = jsonDecode(response.body);
        setState(() {
          _code = data['invite_code'] as String;
          _expiresAt = DateTime.parse(data['expires_at'] as String).toLocal();
        });
      } else {
        setState(() => _error = _messageFor(response.statusCode));
      }
    } catch (_) {
      // Anything that never reached the server: no signal, a dead tunnel, the
      // 15 s timeout in api_client. Worth separating from a refusal, because
      // the two need different things from the person reading this.
      if (mounted) {
        setState(() => _error =
            'Could not reach the server. Check your connection and try again.');
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  /// One sentence per status, each naming what to do about it. A single
  /// "something went wrong" would collapse "you are not this patient's
  /// caregiver" into "try again", and the second is useless advice for the
  /// first.
  String _messageFor(int status) {
    switch (status) {
      case 403:
        return 'Only a caregiver of ${widget.patientName} can invite someone else.';
      case 404:
        return 'This patient no longer exists.';
      case 401:
        return 'Your sign-in has expired. Sign in again and retry.';
      default:
        return 'Could not create an invite code (error $status). Please try again.';
    }
  }

  String _formatExpiry(DateTime at) {
    final d = at;
    final hh = d.hour.toString().padLeft(2, '0');
    final mm = d.minute.toString().padLeft(2, '0');
    return '${d.day}/${d.month}/${d.year} $hh:$mm';
  }

  Future<void> _copyCode() async {
    await Clipboard.setData(ClipboardData(text: _code!));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Code copied')),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Invite another caregiver')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              widget.patientName,
              style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            const Text(
              'The person you invite will be able to see this patient’s '
              'location and alerts, exactly as you can.',
              style: TextStyle(fontSize: 16, color: Colors.black87),
            ),
            const SizedBox(height: 24),

            if (_code == null) ...[
              ElevatedButton(
                onPressed: _sending ? null : _createInvite,
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
                    : const Text('Create invite code',
                        style: TextStyle(fontSize: 18)),
              ),
            ] else ...[
              Container(
                padding: const EdgeInsets.symmetric(vertical: 28),
                decoration: BoxDecoration(
                  color: Colors.blue.shade50,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.blue.shade200),
                ),
                child: Column(
                  children: [
                    SelectableText(
                      _code!,
                      style: const TextStyle(
                        fontSize: 40,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 4,
                      ),
                    ),
                    if (_expiresAt != null) ...[
                      const SizedBox(height: 10),
                      Text(
                        'Valid until ${_formatExpiry(_expiresAt!)}',
                        style: const TextStyle(fontSize: 15, color: Colors.black54),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 16),
              const Text(
                'Give this code to the other caregiver. They open PathGuard, '
                'sign in, and tap “Join a patient”.\n\n'
                'It works once and then stops — create another if they need a '
                'second try.',
                style: TextStyle(fontSize: 15, color: Colors.black87),
              ),
              const SizedBox(height: 20),
              OutlinedButton.icon(
                onPressed: _copyCode,
                icon: const Icon(Icons.copy),
                label: const Text('Copy code'),
                style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48)),
              ),
              const SizedBox(height: 10),
              TextButton(
                onPressed: _sending ? null : _createInvite,
                child: const Text('Create a different code'),
              ),
            ],

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
