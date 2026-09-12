import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import '../../services/api_client.dart';
import '../../services/caregiver_session.dart';
import '../../services/chat_directory.dart';
import 'notification_screen.dart';

/// C-6, the family chat for one patient's caregivers.
///
/// Three things the report asks of this screen, and only one of them is chat:
/// a group conversation, a way to know something important happened, and
/// each caregiver's live distance from the patient across the top.
///
/// The "something happened" part used to render the full `alerts` feed
/// inline, one red bubble per DB row — a wandering episode logs a row roughly
/// every poll cycle while it holds, so the conversation filled up with
/// repeats of the same warning. It now shows only a single small banner for
/// whichever alert is most recently unresolved; the full history (and where
/// to resolve one) lives on [NotificationScreen], one tap away. Alerts are
/// still *read*, never written, here — `alerts` in Postgres is the one copy
/// of what happened, this screen just points at it.
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
        apiGet('/api/patients/${widget.patientId}/alerts?limit=100'),
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
        const SnackBar(content: Text('กรุณาเข้าสู่ระบบใหม่ก่อนส่งข้อความ')),
      );
      return;
    }

    setState(() => _sending = true);
    try {
      await ChatDirectory.send(
        patientId: widget.patientId,
        senderId: session.caregiverId!,
        senderName: session.caregiverName ?? 'ผู้ดูแล',
        text: text,
      );
      _composer.clear();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('ส่งไม่สำเร็จ ตรวจสอบอินเทอร์เน็ต')),
        );
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  List<ChatMessage> _sortedMessages(List<ChatMessage> messages) {
    final sorted = [...messages];
    sorted.sort((a, b) => a.sentAt.compareTo(b.sentAt));
    return sorted;
  }

  /// The most recently unresolved alert, or null when nothing is active.
  Map<String, dynamic>? _activeAlert() {
    final unresolved = _alerts.where((a) => a['resolved'] != true).toList();
    if (unresolved.isEmpty) return null;
    unresolved.sort((a, b) {
      final at = DateTime.tryParse(a['created_at'] as String? ?? '') ?? DateTime(0);
      final bt = DateTime.tryParse(b['created_at'] as String? ?? '') ?? DateTime(0);
      return bt.compareTo(at);
    });
    return unresolved.first;
  }

  String _clock(DateTime at) =>
      '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';

  /// The distance strip. A caregiver whose position is stale or missing stays
  /// on the list and is shown as "location unknown" rather than dropped --
  /// the same rule the ranking endpoint itself follows, and for the same
  /// reason: an empty list while a patient is missing is the worst answer.
  Widget _buildDistanceStrip() {
    if (_caregivers.isEmpty) return const SizedBox.shrink();
    final myId = CaregiverSession.instance.caregiverId;
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: SizedBox(
        height: 104,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 16),
          itemCount: _caregivers.length,
          separatorBuilder: (_, _) => const SizedBox(width: 18),
          itemBuilder: (context, i) {
            final c = _caregivers[i];
            final distance = (c['distance_m'] as num?)?.toDouble();
            final usable = c['usable'] == true;
            final label = distance == null || !usable
                ? 'ไม่ทราบ'
                : distance >= 1000
                    ? '${(distance / 1000).toStringAsFixed(1)} กม.'
                    : '${distance.round()} ม.';
            final isMe = c['caregiver_id'] == myId;
            // Green only for an explicit "available"; unset reads the same as
            // unavailable here because the dot has no room for a third colour.
            final available = c['is_available'] == true;
            return SizedBox(
              width: 64,
              child: Column(
                children: [
                  Stack(
                    clipBehavior: Clip.none,
                    children: [
                      CircleAvatar(
                        radius: 28,
                        backgroundColor: Colors.grey[200],
                        child: Icon(Icons.person_outline_rounded,
                            size: 30, color: Colors.grey[800]),
                      ),
                      Positioned(
                        right: 0,
                        top: 0,
                        child: Container(
                          width: 16,
                          height: 16,
                          decoration: BoxDecoration(
                            color: available ? Colors.green[500] : Colors.grey[500],
                            shape: BoxShape.circle,
                            border: Border.all(color: Colors.white, width: 2),
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(
                    isMe ? 'คุณ' : c['name'] as String? ?? 'ผู้ดูแล',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 13, color: Colors.grey[800]),
                  ),
                  Text(
                    label,
                    style: TextStyle(fontSize: 12, color: Colors.grey[600]),
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );
  }

  /// A single small pill for whichever alert is most recently unresolved.
  /// Tapping it goes to [NotificationScreen], which has the rest and the
  /// resolve button -- this banner is a heads-up, not the place to act on it.
  Widget _buildActiveAlertBanner() {
    final alert = _activeAlert();
    if (alert == null) return const SizedBox.shrink();
    final at = DateTime.tryParse(alert['created_at'] as String? ?? '')?.toLocal();
    final label = at == null
        ? '${alert['message'] ?? alert['alert_type']}'
        : '${alert['message'] ?? alert['alert_type']} ${_clock(at)}';

    return GestureDetector(
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => const NotificationScreen()),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(8, 8, 8, 0),
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: Colors.amber[50],
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: Colors.amber[200]!),
          ),
          child: Row(
            children: [
              Icon(Icons.warning_amber_rounded, size: 16, color: Colors.amber[800]),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 12.5, color: Colors.amber[900]),
                ),
              ),
            ],
          ),
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
              'แชทครอบครัว',
              style: TextStyle(fontSize: 20, color: Colors.black),
            ),
          ],
        ),
      ),
      body: Column(
        children: [
          _buildDistanceStrip(),
          _buildActiveAlertBanner(),
          Expanded(
            child: StreamBuilder<List<ChatMessage>>(
              stream: ChatDirectory.stream(widget.patientId),
              builder: (context, snapshot) {
                final messages = _sortedMessages(snapshot.data ?? const []);
                if (messages.isEmpty) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Text(
                        'ยังไม่มีข้อความ\nพูดคุยประสานงานกับผู้ดูแลคนอื่นได้ที่นี่',
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
                  itemCount: messages.length,
                  itemBuilder: (context, i) => _buildMessageRow(messages[i]),
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
                        hintText: 'พิมพ์ข้อความถึงครอบครัว',
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
                    label: 'ส่งข้อความ',
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
