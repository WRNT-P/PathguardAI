import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:firebase_database/firebase_database.dart';

/// "This patient is walking somewhere on purpose, right now."
///
/// The caregiver's track screen used to infer travel from whether an alert
/// happened to be unresolved, which answered a different question entirely and
/// left a patient sitting at home reading as "Traveling" for as long as nobody
/// closed the alert. Movement is now measured from the GPS track, and this is
/// the other half: the patient's own device saying where they set out for, so
/// the screen can name the destination instead of guessing from displacement,
/// and can say it the moment they start rather than after enough track has
/// accumulated to prove it.
///
/// It is deliberately NOT the only travel signal. A patient who wanders out
/// without touching the app — the case this whole product exists for — never
/// writes this node, so the track-based check stays as the safety net.
///
/// Stored at `active_trips/{patientId}`, alongside `trip_requests` and scoped
/// to the same family access list.
class ActiveTripService {
  ActiveTripService._();
  static final ActiveTripService instance = ActiveTripService._();

  /// How often the walking device re-stamps `heartbeat`.
  static const Duration heartbeatInterval = Duration(seconds: 60);

  /// A trip whose heartbeat is older than this is treated as over, whatever
  /// the node still says.
  ///
  /// The flag the caregiver screen used before this one got stuck because
  /// nothing was responsible for clearing it. Two things stop that happening
  /// here: `onDisconnect().remove()`, which makes the Firebase *server* delete
  /// the node when the device's socket drops (app killed, battery flat, signal
  /// lost), and this TTL, which covers what onDisconnect cannot — a device
  /// still connected but no longer running the navigation screen. Three
  /// missed heartbeats is the threshold, so one slow write does not end a
  /// live trip.
  static const Duration staleAfter = Duration(minutes: 3);

  static DatabaseReference _refFor(int patientId) =>
      FirebaseDatabase.instance.ref('active_trips/$patientId');

  Timer? _heartbeat;
  DatabaseReference? _current;

  /// Which trip is the live one. Bumped synchronously by every [start], and
  /// checked by [ActiveTripHandle.end] before it deletes anything.
  ///
  /// Without it the SOS redirect mid-walk wipes its own replacement: it uses
  /// `pushReplacement`, so the new navigation screen's `initState` writes the
  /// safe-place trip and only *then* is the old screen disposed — a plain
  /// "clear the node" in `dispose` would delete the trip that had just
  /// started, leaving the caregiver's panel blank exactly when the patient
  /// had pressed SOS.
  int _generation = 0;

  /// Mark a trip started, superseding any trip already running.
  ///
  /// Returns synchronously so the caller holds a handle for the trip *it*
  /// started, whatever happens afterwards; the write itself continues in the
  /// background. Never throws: a Realtime Database that is unreachable must
  /// not stop a patient from being navigated home. The cost of failing is
  /// that the caregiver's panel falls back to the track-based reading, which
  /// is the behaviour they had before this existed.
  ActiveTripHandle start({
    required int patientId,
    required Map<String, dynamic> place,
  }) {
    final generation = ++_generation;
    unawaited(_start(patientId, place, generation));
    return ActiveTripHandle._(this, generation);
  }

  Future<void> _start(
      int patientId, Map<String, dynamic> place, int generation) async {
    _heartbeat?.cancel();
    _heartbeat = null;

    final ref = _refFor(patientId);
    _current = ref;
    try {
      // Registered before the write, so there is no window in which the node
      // exists without the server already knowing to clean it up.
      await ref.onDisconnect().remove();
      await ref.set({
        'destination_name': place['name'],
        'latitude': place['lat'],
        'longitude': place['lng'],
        'started_at': ServerValue.timestamp,
        'heartbeat': ServerValue.timestamp,
      });
    } catch (e) {
      debugPrint('active trip: could not start — $e');
      return;
    }

    // A newer trip began while those writes were in flight; it owns the node
    // and its own heartbeat now.
    if (generation != _generation) return;

    _heartbeat = Timer.periodic(heartbeatInterval, (_) async {
      try {
        await ref.update({'heartbeat': ServerValue.timestamp});
      } catch (e) {
        debugPrint('active trip: heartbeat failed — $e');
      }
    });
  }

  Future<void> _endIfCurrent(int generation) async {
    // Superseded — the trip this caller started is already over, and the node
    // belongs to somebody else.
    if (generation != _generation) return;
    _heartbeat?.cancel();
    _heartbeat = null;
    final ref = _current;
    _current = null;
    if (ref == null) return;
    try {
      // Cancelled first: leaving it armed would queue a delete against
      // whatever a later trip writes to the same path.
      await ref.onDisconnect().cancel();
      await ref.remove();
    } catch (e) {
      debugPrint('active trip: could not clear — $e');
    }
  }

  /// The patient's current trip, or null when they are not on one.
  ///
  /// Emits null rather than a stale trip once the heartbeat goes cold, so a
  /// reader never has to remember to check the age itself.
  Stream<ActiveTrip?> watch(int patientId) {
    return _refFor(patientId).onValue.map((event) {
      final value = event.snapshot.value;
      if (value is! Map) return null;
      final trip = ActiveTrip.fromMap(Map<String, dynamic>.from(value));
      return trip.isFresh ? trip : null;
    }).handleError((Object e) {
      debugPrint('active trip: watch failed — $e');
    });
  }
}

/// One screen's claim on the active-trip node. Ending it is a no-op once a
/// later trip has taken over — see [ActiveTripService._generation].
class ActiveTripHandle {
  final ActiveTripService _service;
  final int _generation;

  const ActiveTripHandle._(this._service, this._generation);

  Future<void> end() => _service._endIfCurrent(_generation);
}

class ActiveTrip {
  final String? destinationName;
  final double? latitude;
  final double? longitude;
  final DateTime? heartbeatAt;

  const ActiveTrip({
    this.destinationName,
    this.latitude,
    this.longitude,
    this.heartbeatAt,
  });

  factory ActiveTrip.fromMap(Map<String, dynamic> map) {
    final beat = map['heartbeat'];
    return ActiveTrip(
      destinationName: map['destination_name'] as String?,
      latitude: (map['latitude'] as num?)?.toDouble(),
      longitude: (map['longitude'] as num?)?.toDouble(),
      // ServerValue.timestamp resolves to milliseconds on the Firebase clock,
      // which is why the walking device stamps it rather than sending its own
      // — two phones with drifting clocks would otherwise disagree about
      // whether the same trip is still alive.
      heartbeatAt: beat is num
          ? DateTime.fromMillisecondsSinceEpoch(beat.toInt())
          : null,
    );
  }

  bool get isFresh =>
      heartbeatAt != null &&
      DateTime.now().difference(heartbeatAt!) <= ActiveTripService.staleAfter;
}
