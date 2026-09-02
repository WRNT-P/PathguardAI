import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:firebase_database/firebase_database.dart';
import 'api_client.dart';

enum TripRequestStatus { pending, approved, rejected }

TripRequestStatus _statusFromString(String value) {
  switch (value) {
    case 'approved':
      return TripRequestStatus.approved;
    case 'rejected':
      return TripRequestStatus.rejected;
    default:
      return TripRequestStatus.pending;
  }
}

class TripRequest {
  final String id;
  final String patientName;
  final Map<String, dynamic> place;
  final double? confidence;
  final int? backendId;
  TripRequestStatus status;
  final Completer<bool> _decision = Completer<bool>();

  TripRequest({
    required this.id,
    required this.patientName,
    required this.place,
    this.confidence,
    this.backendId,
    this.status = TripRequestStatus.pending,
  });

  Future<bool> get decision => _decision.future;

  void _complete(bool approved) {
    if (_decision.isCompleted) return;
    status = approved ? TripRequestStatus.approved : TripRequestStatus.rejected;
    _decision.complete(approved);
  }
}

/// Firebase-backed — syncs trip requests between a patient's device and a
/// caregiver's device in real time. Path is deliberately flat
/// (trip_requests/{requestId}), not scoped per patient/caregiver, since the
/// app has no real pairing/login yet — see project memory for the reasoning.
class TripRequestDirectory extends ChangeNotifier {
  TripRequestDirectory._() {
    _ref.onValue.listen(_onSnapshot);
  }
  static final TripRequestDirectory instance = TripRequestDirectory._();

  final DatabaseReference _ref = FirebaseDatabase.instance.ref('trip_requests');

  List<TripRequest> requests = [];
  final Map<String, TripRequest> _liveRequests = {};

  void _onSnapshot(DatabaseEvent event) {
    final data = event.snapshot.value;
    if (data is! Map) {
      requests = [];
      notifyListeners();
      return;
    }

    requests = data.entries.map((entry) {
      final id = entry.key as String;
      final map = Map<String, dynamic>.from(entry.value as Map);
      final status = _statusFromString(map['status'] as String? ?? 'pending');

      // Reuse the same TripRequest object for a request we created locally,
      // so its `decision` Completer (awaited by requestTripApproval) is the
      // one that actually resolves — a fresh object here would have its own
      // Completer that nobody is listening to.
      final existing = _liveRequests[id];
      final request = existing ??
          TripRequest(
            id: id,
            patientName: map['patientName'] as String,
            place: Map<String, dynamic>.from(map['place'] as Map),
            confidence: (map['confidence'] as num?)?.toDouble(),
            backendId: (map['backendId'] as num?)?.toInt(),
          );
      request.status = status;
      if (status != TripRequestStatus.pending) {
        request._complete(status == TripRequestStatus.approved);
      }
      return request;
    }).toList();

    notifyListeners();
  }

  Future<TripRequest> create({
    required String patientName,
    required Map<String, dynamic> place,
    double? confidence,
    int? backendId,
  }) async {
    final ref = _ref.push();
    final id = ref.key!;
    final request = TripRequest(
      id: id,
      patientName: patientName,
      place: place,
      confidence: confidence,
      backendId: backendId,
    );
    _liveRequests[id] = request;

    await ref.set({
      'patientName': patientName,
      'place': place,
      'status': 'pending',
      if (confidence != null) 'confidence': confidence,
      if (backendId != null) 'backendId': backendId,
    });

    return request;
  }

  Future<void> decide(String id, bool approved) async {
    await _ref.child(id).update({'status': approved ? 'approved' : 'rejected'});

    final backendId = _liveRequests[id]?.backendId;
    if (backendId == null) return;

    try {
      await apiPatch('/api/trip-requests/$backendId', body: {
        'decision': approved ? 'approve' : 'reject',
      });
    } catch (_) {
    }
  }

  List<TripRequest> get pending =>
      requests.where((r) => r.status == TripRequestStatus.pending).toList();
}
