import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import '../../services/api_client.dart';
import '../../services/caregiver_session.dart';
import '../../services/chat_directory.dart';

/// C-6, the family chat for one patient's caregivers.
///
/// Three things the report asks of this screen, and only one of them is chat:
/// a group conversation, automatic system messages when something important
/// happens, and each caregiver's live distance from the patient across the top.
///
/// The system messages are *read*, never written. Every caregiver's phone would
/// otherwise race to write the same "went off route at 10:32" line into the
/// room and the family would see it two or three times; worse, the alert would
/// then exist in two places that can disagree. `alerts` in Postgres is already
/// the record of what happened, so this screen folds that feed into the
/// timeline and writes nothing. Nothing has to stay in sync because there is
/// only one copy.
class ChatScreen extends StatefulWidget {
  final int patientId;
  final String patientName;

  const ChatScreen({
    super.key,
    required this.patientId,
    required this.patientName,
  });

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

/// One row in the merged timeline: either something a person typed, or
/// something the system recorded.
class _Entry {
  final DateTime at;
  final ChatMessage? message;
  final Map<String, dynamic>? alert;

  const _Entry.chat(this.message, this.at) : alert = null;
  const _Entry.system(this.alert, this.at) : message = null;
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _composer = TextEditingController();
  final ScrollController _scroll = ScrollController();

  List<Map<String, dynamic>> _alerts = [];
  List<Map<String, dynamic>> _caregivers = [];
  Timer? _refresh;
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    _loadContext();
    // Same 15 s cadence the track screen uses. Both reads are plain selects --
    // unlike GET /api/risk, neither writes a row or can push a notification,
    // so polling them is safe.
    _refresh = Timer.periodic(const Duration(seconds: 15), (_) => _loadContext());
  }

  @override
  void dispose() {
    _refresh?.cancel();
    _composer.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _loadContext() async {
    try {
      final results = await Future.wait([
        apiGet('/api/patients/${widget.patientId}/alerts'),
        apiGet('/api/patients/${widget.patientId}/caregivers'),
      ]);
      if (!mounted) return;
      setState(() {
        if (results[0].statusCode == 200) {
          _alerts = (jsonDecode(results[0].body)['alerts'] as List)
              .cast<Map<String, dynamic>>();
        }
        if (results[1].statusCode == 200) {
          _caregivers = (jsonDecode(results[1].body)['caregivers'] as List)
              .cast<Map<String, dynamic>>();
        }
      });
    } catch (_) {
      // Leave whatever was already on screen. A dropped poll is not news, and
      // blanking the conversation because one read timed out would be worse
      // than showing it a few seconds stale.
    }
  }

  Future<void> _send() async {
    final session = CaregiverSession.instance;
    final text = _composer.text.trim();
    if (text.isEmpty || _sending) return;

    // Say so rather than swallowing it. Without an id there is nobody to
    // attribute the message to, and a send button that silently does nothing
    // is the failure mode this project has already paid for twice.
    if (session.caregiverId == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Sign in again before sending a message.')),
      );
      return;
    }

    setState(() => _sending = true);
    try {
      await ChatDirectory.send(
        patientId: widget.patientId,
        senderId: session.caregiverId!,
        senderName: session.caregiverName ?? 'Caregiver',
        text: text,
      );
      _composer.clear();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not send. Check your connection.')),
        );
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  /// Merge the two sources into one time-ordered list.
  List<_Entry> _timeline(List<ChatMessage> messages) {
    final entries = <_Entry>[
      for (final m in messages) _Entry.chat(m, m.sentAt),
    ];

    for (final alert in _alerts) {
      final raw = alert['created_at'] as String?;
      final at = raw == null ? null : DateTime.tryParse(raw)?.toLocal();
      if (at != null) entries.add(_Entry.system(alert, at));
    }

    entries.sort((a, b) => a.at.compareTo(b.at));
    return entries;
  }

  String _clock(DateTime at) =>
      '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';

  /// The distance strip. A caregiver whose position is stale or missing stays
  /// on the list and is shown as "location unknown" rather than dropped --
  /// the same rule the ranking endpoint itself follows, and for the same
  /// reason: an empty list while a patient is missing is the worst answer.
  Widget _buildDistanceStrip() {
    if (_caregivers.isEmpty) return const SizedBox.shrink();
    return Container(
      color: Colors.grey[100],
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: SizedBox(
        height: 62,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 14),
          itemCount: _caregivers.length,
          separatorBuilder: (_, _) => const SizedBox(width: 10),
          itemBuilder: (context, i) {
            final c = _caregivers[i];
            final distance = (c['distance_m'] as num?)?.toDouble();
            final usable = c['usable'] == true;
            final label = distance == null || !usable
                ? 'Location unknown'
                : distance >= 1000
                    ? '${(distance / 1000).toStringAsFixed(1)} km away'
                    : '${distance.round()} m away';
            return Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: Colors.grey[300]!),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    c['name'] as String? ?? 'Caregiver',
                    style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    label,
                    style: TextStyle(
                      fontSize: 11.5,
                      color: usable && distance != null ? Colors.blue[800] : Colors.grey[600],
                    ),
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );
  }

  Widget _buildSystemRow(Map<String, dynamic> alert, DateTime at) {
    final critical = alert['severity'] == 'critical' || alert['severity'] == 'high';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: critical ? Colors.red[50] : Colors.grey[200],
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          children: [
            Icon(
              critical ? Icons.warning_amber_rounded : Icons.info_outline_rounded,
              size: 16,
              color: critical ? Colors.red[700] : Colors.grey[700],
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                '${alert['message'] ?? alert['alert_type']} - ${_clock(at)}',
                style: TextStyle(
                  fontSize: 12.5,
                  color: critical ? Colors.red[900] : Colors.grey[800],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildMessageRow(ChatMessage message) {
    final mine = message.senderId == CaregiverSession.instance.caregiverId;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4, horizontal: 8),
      child: Column(
        crossAxisAlignment: mine ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        children: [
          if (!mine)
            Padding(
              padding: const EdgeInsets.only(left: 6, bottom: 2),
              child: Text(
                message.senderName,
                style: TextStyle(
                  fontSize: 11.5,
                  color: Colors.grey[700],
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          Container(
            constraints: const BoxConstraints(maxWidth: 280),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              color: mine ? Colors.blue : Colors.grey[200],
              borderRadius: BorderRadius.circular(16),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  message.text,
                  style: TextStyle(
                    fontSize: 15,
                    color: mine ? Colors.white : Colors.black87,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  _clock(message.sentAt),
                  style: TextStyle(
                    fontSize: 10.5,
                    color: mine ? Colors.white70 : Colors.grey[600],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0.5,
        foregroundColor: Colors.black87,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              widget.patientName,
              style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
            ),
            Text(
              'Family chat',
              style: TextStyle(fontSize: 12, color: Colors.grey[600]),
            ),
          ],
        ),
      ),
      body: Column(
        children: [
          _buildDistanceStrip(),
          Expanded(
            child: StreamBuilder<List<ChatMessage>>(
              stream: ChatDirectory.stream(widget.patientId),
              builder: (context, snapshot) {
                final entries = _timeline(snapshot.data ?? const []);
                if (entries.isEmpty) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Text(
                        'No messages yet.\nCoordinate with the other caregivers here.',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: Colors.grey[600], fontSize: 14),
                      ),
                    ),
                  );
                }
                WidgetsBinding.instance.addPostFrameCallback((_) {
                  if (_scroll.hasClients) {
                    _scroll.jumpTo(_scroll.position.maxScrollExtent);
                  }
                });
                return ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  itemCount: entries.length,
                  itemBuilder: (context, i) {
                    final entry = entries[i];
                    return entry.message != null
                        ? _buildMessageRow(entry.message!)
                        : _buildSystemRow(entry.alert!, entry.at);
                  },
                );
              },
            ),
          ),
          SafeArea(
            top: false,
            child: Container(
              padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
              decoration: BoxDecoration(
                color: Colors.white,
                border: Border(top: BorderSide(color: Colors.grey[300]!)),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _composer,
                      textInputAction: TextInputAction.send,
                      onSubmitted: (_) => _send(),
                      decoration: InputDecoration(
                        hintText: 'Message the family',
                        filled: true,
                        fillColor: Colors.grey[100],
                        contentPadding:
                            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(24),
                          borderSide: BorderSide.none,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 6),
                  Semantics(
                    label: 'Send message',
                    button: true,
                    child: IconButton(
                      icon: _sending
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.send_rounded),
                      color: Colors.blue,
                      onPressed: _sending ? null : _send,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
