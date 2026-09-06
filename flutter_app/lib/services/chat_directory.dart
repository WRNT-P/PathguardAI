import 'package:firebase_database/firebase_database.dart';

/// One thing a caregiver typed into a patient's family chat.
class ChatMessage {
  final String id;
  final int senderId;
  final String senderName;
  final String text;
  final DateTime sentAt;

  const ChatMessage({
    required this.id,
    required this.senderId,
    required this.senderName,
    required this.text,
    required this.sentAt,
  });
}

/// Firebase-backed group chat, one room per patient.
///
/// Firebase rather than the backend because the report's own stack sheet puts
/// In-app Chat in the Realtime Database next to Live Tracking, and because a
/// chat is the one thing here that genuinely wants a push socket — polling
/// `/api/...` every few seconds to find out whether a relative typed something
/// is the wrong shape. `trip_request_directory.dart` already talks to RTDB the
/// same way, so this adds no dependency and no backend surface.
///
/// The room is keyed on the patient, not on the caregiver, because that is what
/// the family is coordinating *about*: two siblings looking after the same
/// parent must land in the same room, and a caregiver looking after two parents
/// must not have those two conversations collide. `patient_caregivers` already
/// decides who belongs to which room; this only has to agree with it.
class ChatDirectory {
  ChatDirectory._();

  static DatabaseReference _room(int patientId) =>
      FirebaseDatabase.instance.ref('chats/$patientId/messages');

  /// Newest 200 messages, oldest first.
  ///
  /// Ordered by push key rather than by a `sent_at` child on purpose: RTDB push
  /// ids are already chronological by construction, so key order needs no
  /// `.indexOn` rule in the console and cannot silently fall back to a
  /// client-side sort the way `orderByChild` does on an unindexed path.
  static Stream<List<ChatMessage>> stream(int patientId) {
    return _room(patientId).limitToLast(200).onValue.map((event) {
      final data = event.snapshot.value;
      if (data is! Map) return <ChatMessage>[];

      final messages = data.entries.map((entry) {
        final map = Map<String, dynamic>.from(entry.value as Map);
        // sent_at is written as a server timestamp, so it arrives as an int of
        // epoch milliseconds. It can be briefly null on the sender's own device
        // between the optimistic local write and the server's echo — fall back
        // to now rather than dropping the message the author just typed.
        final sentAt = map['sent_at'];
        return ChatMessage(
          id: entry.key as String,
          senderId: (map['sender_id'] as num?)?.toInt() ?? 0,
          senderName: map['sender_name'] as String? ?? 'Caregiver',
          text: map['text'] as String? ?? '',
          sentAt: sentAt is int
              ? DateTime.fromMillisecondsSinceEpoch(sentAt)
              : DateTime.now(),
        );
      }).toList();

      messages.sort((a, b) => a.id.compareTo(b.id));
      return messages;
    });
  }

  static Future<void> send({
    required int patientId,
    required int senderId,
    required String senderName,
    required String text,
  }) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty) return;
    await _room(patientId).push().set({
      'sender_id': senderId,
      'sender_name': senderName,
      'text': trimmed,
      // Stamped by Firebase, never by the phone — a caregiver with a wrong
      // clock would otherwise sort their message to the top or bottom of
      // everyone else's conversation.
      'sent_at': ServerValue.timestamp,
    });
  }
}
