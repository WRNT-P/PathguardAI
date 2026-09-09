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

  /// Which patient asked. Carried so a decision knows which node to write
  /// back to, now that requests live under the patient they belong to.
  final int patientId;

  final String patientName;
  final Map<String, dynamic> place;
  final double? confidence;
  final int? backendId;
  TripRequestStatus status;
  final Completer<bool> _decision = Completer<bool>();

  TripRequest({
    required this.id,
    required this.patientId,
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

/// Firebase-backed — syncs trip requests between a patient's device and their
/// caregivers' devices in real time.
///
/// Stored per patient at `trip_requests/{patientId}/{requestId}`. It was one
/// flat node once, back when the app had no real login: every caregiver's app
/// listened to the whole thing and so held every other family's requests, and
/// the database rules could not do better than "is anybody signed in" because
/// there was nothing in the path to check a name against.
///
/// Watching is therefore explicit — [watch] with the patients this device is
/// entitled to. A caregiver app calls it with their patient list, a patient's
/// app with its own id; nothing subscribes on its own, so no screen can
/// quietly re-open the door by existing.
class TripRequestDirectory extends ChangeNotifier {
  TripRequestDirectory._();
  static final TripRequestDirectory instance = TripRequestDirectory._();

  static DatabaseReference _refFor(int patientId) =>
      FirebaseDatabase.instance.ref('trip_requests/$patientId');

  final Map<int, StreamSubscription<DatabaseEvent>> _subscriptions = {};
  final Map<int, List<TripRequest>> _byPatient = {};

  /// Requests this device is locally waiting on a decision for, by id — see
  /// the reuse note in [_onSnapshot].
  final Map<String, TripRequest> _liveRequests = {};

  List<TripRequest> requests = [];

  /// Follow exactly these patients, and no others.
  ///
  /// Idempotent: called again with the same ids it does nothing, so a screen
  /// may call it on every rebuild. Patients dropped from the list are
  /// unsubscribed and their requests forgotten — a caregiver who loses access
  /// should stop seeing them without waiting for a restart.
  void watch(Iterable<int> patientIds) {
    final wanted = patientIds.toSet();

    for (final gone in _subscriptions.keys.toSet().difference(wanted)) {
      _subscriptions.remove(gone)?.cancel();
      _byPatient.remove(gone);
    }

    for (final id in wanted.difference(_subscriptions.keys.toSet())) {
      _subscriptions[id] =
          _refFor(id).onValue.listen((event) => _onSnapshot(id, event));
    }

    _rebuild();
  }

  /// Stop following everything. For sign-out: the next account on this device
  /// must not inherit the last one's rooms.
  void clear() {
    for (final sub in _subscriptions.values) {
      sub.cancel();
    }
    _subscriptions.clear();
    _byPatient.clear();
    _liveRequests.clear();
    _rebuild();
  }

  void _onSnapshot(int patientId, DatabaseEvent event) {
    final data = event.snapshot.value;
    if (data is! Map) {
      _byPatient[patientId] = [];
      _rebuild();
      return;
    }

    _byPatient[patientId] = data.entries.map((entry) {
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
            patientId: patientId,
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

    _rebuild();
  }

  void _rebuild() {
    requests = [
      for (final list in _byPatient.values) ...list,
    ];
    notifyListeners();
  }

  Future<TripRequest> create({
    required int patientId,
    required String patientName,
    required Map<String, dynamic> place,
    double? confidence,
    int? backendId,
  }) async {
    // The asking device has to be following its own node, or the answer
    // arrives at a listener that does not exist and `decision` never
    // completes — the patient waits on a caregiver who already replied.
    watch({..._subscriptions.keys, patientId});

    final ref = _refFor(patientId).push();
    final id = ref.key!;
    final request = TripRequest(
      id: id,
      patientId: patientId,
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

  Future<void> decide(TripRequest request, bool approved) async {
    await _refFor(request.patientId)
        .child(request.id)
        .update({'status': approved ? 'approved' : 'rejected'});

    final backendId = request.backendId;
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
