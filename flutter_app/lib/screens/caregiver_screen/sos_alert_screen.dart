import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'package:url_launcher/url_launcher.dart';
import '../../services/active_trip_service.dart';
import '../../services/api_client.dart';
import '../../services/caregiver_session.dart';
import 'caregiver_navigation_screen.dart';

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

  /// Carried only so the navigation screen can draw them as their own face.
  /// Null when the alert arrived by push, which knows ids and not photos.
  final File? profileImage;

  const SosAlertScreen({
    super.key,
    required this.patientId,
    required this.patientName,
    required this.alert,
    this.profileImage,
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

  /// Where the patient's own app is walking them, live.
  ///
  /// The SOS itself no longer waits for that answer before leaving the phone —
  /// telling the family is the urgent part — so the destination arrives here a
  /// moment later, from the node the walk publishes when it starts.
  ActiveTrip? _activeTrip;
  StreamSubscription<ActiveTrip?>? _activeTripSubscription;

  /// Set the instant this screen starts going away, by any of its four exits.
  ///
  /// They can race: marking an alert resolved pops immediately, and the 8s
  /// poll then reads back the row it just changed, sees `resolved`, and pops
  /// a second time — taking the patient list with it and leaving a black
  /// screen. Claiming has the same shape, where the stray pop would close the
  /// navigation screen that had just replaced this one.
  bool _leaving = false;
  // Tracks whether the claim popup has already been shown for THIS alert, so
  // an 8s poll after it's dismissed doesn't show it again while claimed_by
  // stays the same person.
  bool _claimPopupShown = false;

  @override
  void initState() {
    super.initState();
    _refresh();
    _loadPrediction();
    _refreshTimer = Timer.periodic(
      const Duration(seconds: 8),
      (_) => _refresh(),
    );
    _activeTripSubscription =
        ActiveTripService.instance.watch(widget.patientId).listen((trip) {
      if (mounted) setState(() => _activeTrip = trip);
    });
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
    } catch (_) {}
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
  /// Where the patient's own app is walking them, when it told us.
  ///
  /// Takes the place of the prediction card whenever it exists, because it
  /// beats it outright: Module 2 guesses from travel history, this is the
  /// destination the patient is being led to right now. And the coordinates
  /// on this screen are stale the moment they set off, so this is the part a
  /// caregiver can actually drive to.
  Widget _destinationCard(String destination) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: Colors.blue[50],
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: Colors.blue[200]!),
        ),
        child: Row(
          children: [
            Icon(Icons.directions_walk, color: Colors.blue[700]),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'กำลังเดินไป $destination',
                    style: const TextStyle(
                      fontWeight: FontWeight.w600,
                      fontSize: 16,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'แอปของผู้ป่วยกำลังนำทางไปสถานที่ปลอดภัยนี้ ตำแหน่งจริงจึงอาจไม่ตรงกับบนแผนที่แล้ว',
                    style: TextStyle(fontSize: 12, color: Colors.grey[700]),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

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
      headline = 'ยังคาดเดาจุดหมายไม่ได้';
      caveat = top == null && historyStatus != 'none'
          ? 'สถานที่ที่บันทึกไว้ยังไม่มีชื่อ จึงบอกชื่อจุดหมายไม่ได้'
          : 'ยังไม่มีประวัติการเดินทางระหว่างสถานที่ที่บันทึกไว้ จึงยังคาดเดาไม่ได้';
      background = Colors.grey[100]!;
      borderColour = Colors.grey[300]!;
    } else if (historyStatus == 'ok') {
      headline =
          'น่าจะกำลังไป ${top['place_name']} '
          '(${top['probability_pct']}%)';
      caveat = 'อ้างอิงจากการเดินทาง $observed ครั้งใน 30 วันที่ผ่านมา';
      background = Colors.orange[50]!;
      borderColour = Colors.orange[200]!;
    } else {
      // sparse — a real number off a history too thin to lean on. Shown,
      // because it is the only signal there is, and captioned so nobody
      // mistakes it for the case above.
      headline =
          'อาจกำลังไป ${top['place_name']} '
          '(${top['probability_pct']}%)';
      caveat =
          'ความมั่นใจต่ำ มีข้อมูลการเดินทางแค่ $observed ครั้งใน 30 วันที่ผ่านมา ใช้เป็นแนวทางเท่านั้น';
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
            Text(headline, style: const TextStyle(fontWeight: FontWeight.w600)),
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
    _activeTripSubscription?.cancel();
    super.dispose();
  }

  /// The one way out. Every exit goes through here so no two can fire.
  void _close() {
    if (_leaving) return;
    _leaving = true;
    _refreshTimer?.cancel();
    if (!mounted) return;
    // A bare pop() closes whatever is on top. With the "someone is going"
    // dialog up, that was the dialog — this screen stayed, and _leaving then
    // turned every later exit (the Close button included) into a no-op.
    final route = ModalRoute.of(context);
    final navigator = Navigator.of(context);
    if (route != null) navigator.popUntil((r) => r == route);
    navigator.pop();
  }

  Future<void> _refresh() async {
    if (_leaving) return;
    try {
      final alertsRes = await apiGet(
        '/api/patients/${widget.patientId}/alerts?limit=100',
      );
      if (alertsRes.statusCode == 200) {
        final alerts = jsonDecode(alertsRes.body)['alerts'] as List;
        final updated = alerts.cast<Map<String, dynamic>>().firstWhere(
          (a) => a['id'] == _alert['id'],
          orElse: () => _alert,
        );
        final myId = CaregiverSession.instance.caregiverId;
        final newlyClaimedByOther =
            _alert['claimed_by'] == null &&
            updated['claimed_by'] != null &&
            updated['claimed_by'] != myId;
        if (mounted) setState(() => _alert = updated);
        if (updated['resolved'] == true) {
          _close();
          return;
        }
        // Someone else just claimed it while this screen was open — this is
        // the caregiver's own exit path (removed the unconditional X so
        // nobody dismisses an unclaimed SOS by accident), gated behind
        // actually seeing who's responding.
        if (newlyClaimedByOther && !_claimPopupShown && mounted) {
          _claimPopupShown = true;
          await _showClaimAcknowledgement(
            updated['claimed_by_name'] as String?,
          );
        }
      }

      final rankRes = await apiGet(
        '/api/patients/${widget.patientId}/caregivers',
      );
      if (rankRes.statusCode == 200) {
        final ranked = jsonDecode(rankRes.body)['caregivers'] as List;
        // The endpoint lists every caregiver of the patient, the one reading
        // this screen included — and a call button to yourself is noise.
        final myId = CaregiverSession.instance.caregiverId;
        final others = ranked
            .cast<Map<String, dynamic>>()
            .where((c) => c['caregiver_id'] != myId)
            .toList();
        if (mounted) setState(() => _rankedCaregivers = others);
      }
    } catch (_) {}
  }

  /// Shown to every OTHER caregiver still on this screen once someone claims
  /// the alert. "รับทราบ" both dismisses the dialog and closes the full-screen
  /// alert for them — they've seen who's going, there's nothing left to do
  /// here, and the alert itself stays unresolved until that person is back.
  Future<void> _showClaimAcknowledgement(String? claimerName) async {
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AlertDialog(
        title: const Text('มีคนไปรับแล้ว'),
        content: Text('${claimerName ?? "ผู้ดูแลอีกคน"} กำลังไปรับผู้ป่วย'),
        actions: [
          ElevatedButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: const Text('รับทราบ'),
          ),
        ],
      ),
    );
    _close();
  }

  Future<void> _claim() async {
    setState(() => _acting = true);
    try {
      final res = await apiPost('/api/alerts/${_alert['id']}/claim');
      if (res.statusCode == 200) {
        // Claiming means "I am setting off", so the next thing this caregiver
        // needs is the way there — not this screen again. It replaces the
        // alert rather than stacking on it: coming back from navigation
        // should land on the patient list, not on the alert they answered.
        // Other caregivers learn about the claim on their own _refresh() poll.
        if (mounted && !_leaving) {
          _leaving = true;
          _refreshTimer?.cancel();
          Navigator.of(context).pushReplacement(
            MaterialPageRoute(
              builder: (context) => CaregiverNavigationScreen(
                patientId: widget.patientId,
                patientName: widget.patientName,
                initialLatitude: (_alert['latitude'] as num?)?.toDouble(),
                initialLongitude: (_alert['longitude'] as num?)?.toDouble(),
                alertMessage: _alert['message'] as String?,
                profileImage: widget.profileImage,
                alertId: _alert['id'] as int?,
              ),
            ),
          );
        }
        return;
      } else if (res.statusCode == 409 && mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('มีคนรับเรื่องนี้ไปแล้ว')));
        await _refresh();
      } else if (mounted) {
        // Anything else used to fall through in silence: the button just did
        // nothing and the screen stayed open with no explanation, which is
        // how a 401 from an auth-disabled backend went unnoticed for a day.
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('รับเรื่องไม่สำเร็จ (${res.statusCode})')),
        );
      }
    } catch (_) {
    } finally {
      if (mounted) setState(() => _acting = false);
    }
  }

  /// "sos" is the one alert type the backend never auto-resolves — a human
  /// pressed it, so a human decides when it's actually handled. (Every other
  /// type here — safe_zone_exit, geofence, emergency, gps_loss — closes
  /// itself once its triggering condition clears; see risk.py.)
  Future<void> _markResolved() async {
    setState(() => _acting = true);
    try {
      final res = await apiPatch(
        '/api/alerts/${_alert['id']}',
        body: {'resolved': true},
      );
      if (res.statusCode == 200) {
        _close();
        return;
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
    final alertDestination = _alert['destination_name'] as String?;
    final destination = (alertDestination?.isNotEmpty ?? false)
        ? alertDestination
        : _activeTrip?.destinationName;

    return Scaffold(
      backgroundColor: Colors.red[50],
      appBar: AppBar(
        backgroundColor: Colors.red,
        foregroundColor: Colors.white,
        title: Text('SOS — ${widget.patientName}'),
        automaticallyImplyLeading: false,
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(
              _alert['message'] as String? ?? 'ผู้ป่วยต้องการความช่วยเหลือ',
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
          ),
          // The real destination when the patient's app told us one — on the
          // alert if it carried one, otherwise off the live trip — and only
          // failing both, the guess.
          if ((destination ?? '').isNotEmpty)
            _destinationCard(destination!)
          else if (_prediction != null)
            _predictionCard(),
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
                    icon: gmaps.BitmapDescriptor.defaultMarkerWithHue(
                      gmaps.BitmapDescriptor.hueRed,
                    ),
                  ),
                },
              ),
            ),
          if (_rankedCaregivers.isNotEmpty)
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  'ผู้ดูแลที่อยู่ใกล้ที่สุด',
                  style: TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
            ),
          Expanded(
            child: ListView(
              children: _rankedCaregivers.map((c) {
                final distance = c['distance_m'] as num?;
                final phone = c['phone'] as String?;
                return ListTile(
                  leading: const Icon(Icons.person),
                  title: Text(c['name'] as String? ?? 'ไม่มีชื่อ'),
                  subtitle: Text(
                    distance != null
                        ? '${(distance / 1000).toStringAsFixed(1)} กม.'
                        : 'ไม่ทราบตำแหน่ง',
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
                        child: const Text(
                          'ฉันจะไปรับเอง',
                          style: TextStyle(color: Colors.white),
                        ),
                      )
                    : claimedBy == myId
                    ? Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          if (_alert['alert_type'] == 'sos')
                            Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: ElevatedButton(
                                onPressed: _acting ? null : _markResolved,
                                style: ElevatedButton.styleFrom(
                                  backgroundColor: Colors.green,
                                  minimumSize: const Size(double.infinity, 48),
                                ),
                                child: const Text(
                                  'ยืนยันว่าปลอดภัยแล้ว',
                                  style: TextStyle(color: Colors.white),
                                ),
                              ),
                            ),
                          ElevatedButton(
                            onPressed: _acting ? null : _cancelClaim,
                            style: ElevatedButton.styleFrom(
                              minimumSize: const Size(double.infinity, 48),
                            ),
                            child: const Text('ยกเลิกการรับเรื่อง'),
                          ),
                        ],
                      )
                    : Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Container(
                            width: double.infinity,
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                              color: Colors.green[100],
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Text(
                              '${claimedByName ?? "ผู้ดูแลอีกคน"} กำลังไปรับ',
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                          const SizedBox(height: 8),
                          // Someone else is already handling this — safe to
                          // leave without the "nobody dismisses an unclaimed
                          // SOS by accident" concern above, since this
                          // caregiver isn't the one responsible for it.
                          ElevatedButton(
                            onPressed: _close,
                            style: ElevatedButton.styleFrom(
                              minimumSize: const Size(double.infinity, 48),
                            ),
                            child: const Text('ปิด'),
                          ),
                        ],
                      ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
