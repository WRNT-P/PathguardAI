import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';

import '../../services/api_client.dart';
import '../../services/active_trip_service.dart';
import '../../services/caregiver_session.dart';

/// Live trip progress for one patient, shown under their card on the
/// caregiver homepage while [ActiveTripService] reports them underway.
///
/// Everything here is read from data that already exists elsewhere in the
/// app — nothing new was added to the backend for this:
/// * distance from this caregiver — the same `distance_m` the SOS screen's
///   "nearest caregivers" list shows, just picked out for this caregiver's
///   own id instead of listing everyone.
/// * walking speed — the patient's most recent recorded GPS fix already
///   carries a `speed` (m/s); this is the only number on the card the
///   backend computed, not this widget.
/// * departure time — [ActiveTrip.startedAt], written by the patient's own
///   device the moment they set off, parsed but unused until now.
/// * ETA — extrapolated client-side from remaining distance ÷ current speed.
///   Not a routed ETA (no Directions call here) — a rough estimate, and
///   shown as one.
class TripInfoCard extends StatefulWidget {
  final int patientId;
  final ActiveTrip trip;

  const TripInfoCard({super.key, required this.patientId, required this.trip});

  @override
  State<TripInfoCard> createState() => _TripInfoCardState();
}

class _TripInfoCardState extends State<TripInfoCard> {
  static const _distance = Distance();
  static const _pollEvery = Duration(seconds: 30);

  Timer? _timer;
  double? _distanceFromMeKm;
  double? _speedKmh;
  LatLng? _patientLocation;

  @override
  void initState() {
    super.initState();
    _refresh();
    _timer = Timer.periodic(_pollEvery, (_) => _refresh());
  }

  @override
  void didUpdateWidget(covariant TripInfoCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId != widget.patientId) _refresh();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    await Future.wait([_refreshDistance(), _refreshSpeedAndLocation()]);
  }

  Future<void> _refreshDistance() async {
    try {
      final res = await apiGet('/api/patients/${widget.patientId}/caregivers');
      if (res.statusCode != 200) return;
      final list = (jsonDecode(res.body)['caregivers'] as List)
          .cast<Map<String, dynamic>>();
      final myId = CaregiverSession.instance.caregiverId;
      Map<String, dynamic>? mine;
      for (final c in list) {
        if (c['caregiver_id'] == myId) {
          mine = c;
          break;
        }
      }
      final metres = (mine?['distance_m'] as num?)?.toDouble();
      if (!mounted) return;
      setState(() => _distanceFromMeKm = metres == null ? null : metres / 1000);
    } catch (_) {
      // Leave the last known value up — a dropped poll isn't news.
    }
  }

  Future<void> _refreshSpeedAndLocation() async {
    try {
      final res = await apiGet(
        '/api/patients/${widget.patientId}/track',
        queryParams: {'hours': '1'},
      );
      if (res.statusCode != 200) return;
      final points =
          (jsonDecode(res.body)['points'] as List).cast<Map<String, dynamic>>();
      if (points.isEmpty) return;
      final last = points.last;
      final speedMs = (last['speed'] as num?)?.toDouble();
      if (!mounted) return;
      setState(() {
        _patientLocation = LatLng(
          (last['latitude'] as num).toDouble(),
          (last['longitude'] as num).toDouble(),
        );
        _speedKmh = speedMs == null ? null : speedMs * 3.6;
      });
    } catch (_) {
    }
  }

  /// Remaining distance to the trip's destination, divided by the patient's
  /// current speed. Null whenever either half is unavailable — a guess built
  /// out of a stale or missing speed is worse than no ETA at all.
  DateTime? get _eta {
    final speed = _speedKmh;
    final here = _patientLocation;
    final destLat = widget.trip.latitude;
    final destLng = widget.trip.longitude;
    if (speed == null || speed <= 0.1 || here == null || destLat == null || destLng == null) {
      return null;
    }
    final remainingKm =
        _distance.as(LengthUnit.Kilometer, here, LatLng(destLat, destLng));
    final hours = remainingKm / speed;
    return DateTime.now().add(Duration(minutes: (hours * 60).round()));
  }

  String _time(DateTime? t) {
    if (t == null) return '-';
    final local = t.toLocal();
    return '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(top: 4, bottom: 14),
      elevation: 0,
      color: Colors.blue[50],
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: Colors.blue[100]!),
      ),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 60,
              height: 60,
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(12),
              ),
              alignment: Alignment.center,
              child: Icon(Icons.directions_walk_rounded, color: Colors.blue[700], size: 30),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if ((widget.trip.destinationName ?? '').isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Text(
                        'กำลังเดินทางไป ${widget.trip.destinationName}',
                        style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14),
                      ),
                    ),
                  _row('ห่างจากคุณ',
                      _distanceFromMeKm == null ? '-' : '${_distanceFromMeKm!.toStringAsFixed(1)} กิโล'),
                  _row('ความเร็วการเดินทาง',
                      _speedKmh == null ? '-' : '${_speedKmh!.toStringAsFixed(1)} กม./ชม.'),
                  _row('ออกเดินทางเวลา', _time(widget.trip.startedAt)),
                  _row('คาดว่าจะถึงภายใน', _time(_eta)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _row(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: TextStyle(fontSize: 13, color: Colors.grey[700])),
          Text(value, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }
}
