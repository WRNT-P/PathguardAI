import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'package:url_launcher/url_launcher.dart';
import '../../services/api_client.dart';
import '../../services/caregiver_session.dart';

/// Full-screen SOS/emergency alert — pops up when the caregiver opens the app
/// while one of their patients has an unresolved sos/emergency/geofence alert.
/// Not a true full-screen-intent notification (doesn't interrupt a closed
/// app) — just shown prominently the next time the app is opened, per user
/// decision. Keeps polling while open so a claim by another caregiver shows
/// up live, and auto-closes once the alert is marked resolved.
class SosAlertScreen extends StatefulWidget {
  final int patientId;
  final String patientName;
  final Map<String, dynamic> alert;
  const SosAlertScreen({
    super.key,
    required this.patientId,
    required this.patientName,
    required this.alert,
  });

  @override
  State<SosAlertScreen> createState() => _SosAlertScreenState();
}

class _SosAlertScreenState extends State<SosAlertScreen> {
  late Map<String, dynamic> _alert = widget.alert;
  List<Map<String, dynamic>> _rankedCaregivers = [];
  Map<String, dynamic>? _prediction;
  bool _acting = false;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _refresh();
    _loadPrediction();
    _refreshTimer = Timer.periodic(const Duration(seconds: 8), (_) => _refresh());
  }

  /// Module 2 — where this patient usually goes next, off the Markov
  /// transition matrix the risk scorer already fits.
  ///
  /// This used to store the answer only when `history_status == 'ok'`, which
  /// needs 20 recorded moves in 30 days (`destination.py:61`). No patient in
  /// testing has ever had close to that — the live rows hold three to ten GPS
  /// points each — so the card was written, wired, and invisible to everybody,
  /// and read from outside as "the prediction was never built".
  ///
  /// Silence was the right instinct and the wrong execution. A thin history
  /// must not be dressed up as a confident answer, but "we cannot say yet, and
  /// here is why" is information a caregiver can act on and an empty box is
  /// not. So all three states are kept and the card says which one it is —
  /// with one hard rule carried over from the endpoint's own docstring: at
  /// `none` the numbers are an equal division rather than a prediction, so
  /// **no percentage is ever shown for it**.
  Future<void> _loadPrediction() async {
    try {
      final res = await apiGet('/api/predict-destination/${widget.patientId}');
      if (res.statusCode != 200) return;
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      if (body['status'] == 'ok') {
        if (mounted) setState(() => _prediction = body);
      }
    } catch (_) {
    }
  }

  /// The first prediction the caregiver can actually be told about.
  ///
  /// A learned cluster has no `place_name` — `place_clustering.py` emits
  /// coordinates and a visit count and nothing a human named — and the API
  /// contract's instruction for that case is to hide the tile rather than
  /// print "unknown". "They are heading to Unknown place" is worse than
  /// saying nothing.
  Map<String, dynamic>? get _topNamedPrediction {
    final list = _prediction?['predictions'] as List?;
    if (list == null) return null;
    for (final p in list.cast<Map<String, dynamic>>()) {
      if (p['place_name'] != null) return p;
    }
    return null;
  }

  /// Module 2's answer, stated at the confidence it actually has.
  ///
  /// Three presentations for the endpoint's three history states, because the
  /// difference between them is the whole point: a caregiver deciding where to
  /// drive needs to know whether "the temple" is a pattern or a guess, and one
  /// orange box saying "Predicted destination" for both teaches them to
  /// distrust it within a week.
  Widget _predictionCard() {
    final historyStatus = _prediction!['history_status'] as String?;
    final observed = _prediction!['transitions_observed'] as int? ?? 0;
    final top = _topNamedPrediction;

    final String headline;
    final String caveat;
    final Color background;
    final Color borderColour;

    if (top == null || historyStatus == 'none') {
      // No percentage here, deliberately. At "none" the numbers the endpoint
      // returns are an equal division across the known places — arithmetic,
      // not a prediction — and its own docstring says not to render them as
      // confidence. Saying so plainly is more use than an empty box.
      headline = 'No destination prediction yet';
      caveat = top == null && historyStatus != 'none'
          ? 'The places on file have no names, so there is nothing to name here.'
          : 'This patient has not been recorded travelling between their saved '
              'places yet, so there is nothing to predict from.';
      background = Colors.grey[100]!;
      borderColour = Colors.grey[300]!;
    } else if (historyStatus == 'ok') {
      headline = 'Likely heading to ${top['place_name']} '
          '(${top['probability_pct']}%)';
      caveat = 'Based on $observed recorded moves in the last 30 days.';
      background = Colors.orange[50]!;
      borderColour = Colors.orange[200]!;
    } else {
      // sparse — a real number off a history too thin to lean on. Shown,
      // because it is the only signal there is, and captioned so nobody
      // mistakes it for the case above.
      headline = 'Possibly heading to ${top['place_name']} '
          '(${top['probability_pct']}%)';
      caveat = 'Low confidence — only $observed recorded moves in the last '
          '30 days. Treat this as a hint, not a destination.';
      background = Colors.amber[50]!;
      borderColour = Colors.amber[300]!;
    }

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: background,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: borderColour),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(headline,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            Text(
              caveat,
              style: TextStyle(fontSize: 12, color: Colors.grey[700]),
            ),
          ],
        ),
      ),
    );
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final alertsRes = await apiGet('/api/patients/${widget.patientId}/alerts');
      if (alertsRes.statusCode == 200) {
        final alerts = jsonDecode(alertsRes.body)['alerts'] as List;
        final updated = alerts.cast<Map<String, dynamic>>().firstWhere(
              (a) => a['id'] == _alert['id'],
              orElse: () => _alert,
            );
        if (mounted) setState(() => _alert = updated);
        if (updated['resolved'] == true) {
          if (mounted) Navigator.of(context).pop();
          return;
        }
      }

      final rankRes = await apiGet('/api/patients/${widget.patientId}/caregivers');
      if (rankRes.statusCode == 200) {
        final ranked = jsonDecode(rankRes.body)['caregivers'] as List;
        if (mounted) setState(() => _rankedCaregivers = ranked.cast<Map<String, dynamic>>());
      }
    } catch (_) {
    }
  }

  Future<void> _claim() async {
    setState(() => _acting = true);
    try {
      final res = await apiPost('/api/alerts/${_alert['id']}/claim');
      if (res.statusCode == 200) {
        setState(() => _alert = jsonDecode(res.body)['alert'] as Map<String, dynamic>);
      } else if (res.statusCode == 409 && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Someone already claimed this')),
        );
        await _refresh();
      }
    } catch (_) {
    } finally {
      if (mounted) setState(() => _acting = false);
    }
  }

  Future<void> _cancelClaim() async {
    setState(() => _acting = true);
    try {
      final res = await apiDelete('/api/alerts/${_alert['id']}/claim');
      if (res.statusCode == 200) {
        setState(() => _alert = jsonDecode(res.body) as Map<String, dynamic>);
      }
    } catch (_) {
    } finally {
      if (mounted) setState(() => _acting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final myId = CaregiverSession.instance.caregiverId;
    final claimedBy = _alert['claimed_by'] as int?;
    final claimedByName = _alert['claimed_by_name'] as String?;
    final lat = (_alert['latitude'] as num?)?.toDouble();
    final lng = (_alert['longitude'] as num?)?.toDouble();

    return Scaffold(
      backgroundColor: Colors.red[50],
      appBar: AppBar(
        backgroundColor: Colors.red,
        foregroundColor: Colors.white,
        title: Text('SOS — ${widget.patientName}'),
        automaticallyImplyLeading: false,
        actions: [
          IconButton(
            icon: const Icon(Icons.close),
            onPressed: () => Navigator.of(context).pop(),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(
              _alert['message'] as String? ?? 'The patient needs help',
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
          ),
          if (_prediction != null) _predictionCard(),
          if (lat != null && lng != null)
            SizedBox(
              height: 220,
              child: gmaps.GoogleMap(
                initialCameraPosition: gmaps.CameraPosition(
                  target: gmaps.LatLng(lat, lng),
                  // Google Maps only draws buildings from roughly zoom 17, and
                  // this map is 220 px tall on an alert somebody has to act on:
                  // the question is "which building are they outside", not
                  // "which district". Anything wider is a map of nothing.
                  zoom: 17.5,
                ),
                markers: {
                  gmaps.Marker(
                    markerId: const gmaps.MarkerId('patient'),
                    position: gmaps.LatLng(lat, lng),
                    icon: gmaps.BitmapDescriptor.defaultMarkerWithHue(gmaps.BitmapDescriptor.hueRed),
                  ),
                },
              ),
            ),
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text('Nearest caregivers', style: TextStyle(fontWeight: FontWeight.w600)),
            ),
          ),
          Expanded(
            child: ListView(
              children: _rankedCaregivers.map((c) {
                final distance = c['distance_m'] as num?;
                final phone = c['phone'] as String?;
                return ListTile(
                  leading: const Icon(Icons.person),
                  title: Text(c['name'] as String? ?? 'Unnamed'),
                  subtitle: Text(
                    distance != null
                        ? '${(distance / 1000).toStringAsFixed(1)} km'
                        : 'Unknown location',
                  ),
                  trailing: phone != null
                      ? IconButton(
                          icon: const Icon(Icons.call, color: Colors.green),
                          onPressed: () => launchUrl(Uri.parse('tel:$phone')),
                        )
                      : null,
                );
              }).toList(),
            ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: SizedBox(
                width: double.infinity,
                child: claimedBy == null
                    ? ElevatedButton(
                        onPressed: _acting ? null : _claim,
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.red,
                          minimumSize: const Size(0, 48),
                        ),
                        child: const Text("I'll go get them", style: TextStyle(color: Colors.white)),
                      )
                    : claimedBy == myId
                        ? ElevatedButton(
                            onPressed: _acting ? null : _cancelClaim,
                            style: ElevatedButton.styleFrom(minimumSize: const Size(0, 48)),
                            child: const Text('Cancel claim'),
                          )
                        : Container(
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                              color: Colors.green[100],
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Text(
                              '${claimedByName ?? "Another caregiver"} is on their way',
                              textAlign: TextAlign.center,
                              style: const TextStyle(fontWeight: FontWeight.w600),
                            ),
                          ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
