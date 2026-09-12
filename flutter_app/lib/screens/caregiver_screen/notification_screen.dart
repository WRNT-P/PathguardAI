import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import '../../services/alert_navigation.dart';
import '../../services/api_client.dart';
import '../../services/trip_request_directory.dart';
import 'sos_alert_screen.dart';

class NotificationScreen extends StatefulWidget {
  const NotificationScreen({super.key});

  @override
  State<NotificationScreen> createState() => _NotificationScreenState();
}

class _NotificationScreenState extends State<NotificationScreen> {
  /// Unresolved alerts (SOS plus everything the family chat used to show
  /// inline — emergency/geofence/safe_zone_exit/gps_loss) across every
  /// patient this caregiver looks after.
  ///
  /// None of these close themselves — something happened, so a person
  /// decides when it is over — so they belong on a list the caregiver can
  /// come back to, not only in a full-screen takeover or a chat bubble they
  /// may have scrolled past.
  List<Map<String, dynamic>> _alerts = [];
  bool _loadingAlerts = true;
  Timer? _poll;
  Set<int> _myPatientIds = {};

  @override
  void initState() {
    super.initState();
    TripRequestDirectory.instance.addListener(_onRequestsChanged);
    _loadAlerts();
    _poll = Timer.periodic(const Duration(seconds: 20), (_) => _loadAlerts());
  }

  @override
  void dispose() {
    _poll?.cancel();
    TripRequestDirectory.instance.removeListener(_onRequestsChanged);
    super.dispose();
  }

  Future<void> _loadAlerts() async {
    try {
      final res = await apiGet('/api/patients');
      if (res.statusCode != 200) return;
      final patients = (jsonDecode(res.body)['patients'] as List)
          .cast<Map<String, dynamic>>();

      final found = <Map<String, dynamic>>[];
      for (final patient in patients) {
        final id = patient['patient_id'] as int;
        final alertsRes = await apiGet('/api/patients/$id/alerts?limit=100');
        if (alertsRes.statusCode != 200) continue;
        final alerts = (jsonDecode(alertsRes.body)['alerts'] as List)
            .cast<Map<String, dynamic>>();
        for (final alert in alerts) {
          final type = alert['alert_type'];
          if (!notificationListAlertTypes.contains(type)) continue;
          final informational = informationalAlertTypes.contains(type);
          // Informational rows (trip started/arrived) are written already
          // resolved — they're a feed entry, not an open condition — so they
          // need their own recency cutoff instead of the resolved==false
          // filter everything else uses, or the feed would grow forever.
          if (informational) {
            final at = DateTime.tryParse(alert['created_at'] as String? ?? '');
            if (at == null ||
                DateTime.now().toUtc().difference(at.toUtc()) >
                    const Duration(hours: 24)) {
              continue;
            }
          } else if (alert['resolved'] != false) {
            continue;
          }
          found.add({...alert, '_patient': patient});
        }
      }

      found.sort((a, b) {
        final at = DateTime.tryParse(a['created_at'] as String? ?? '') ?? DateTime(0);
        final bt = DateTime.tryParse(b['created_at'] as String? ?? '') ?? DateTime(0);
        return bt.compareTo(at);
      });

      if (!mounted) return;
      setState(() {
        _alerts = found;
        _myPatientIds = patients.map((p) => p['patient_id'] as int).toSet();
        _loadingAlerts = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingAlerts = false);
    }
  }

  /// Close an SOS from the list.
  ///
  /// The other way to end one is to claim it, drive there, and press "I've
  /// reached them" — right for a real emergency and painful for the row left
  /// open by a test, a misfire, or a patient who rang instead. Confirmed,
  /// because it changes what every other caregiver sees.
  Future<void> _resolve(Map<String, dynamic> alert) async {
    final patient = alert['_patient'] as Map<String, dynamic>;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('ปิด SOS ของ ${patient['name']} ใช่ไหม'),
        content: const Text(
          'การแจ้งเตือนนี้จะถูกปิดสำหรับทุกคน กดเมื่อแน่ใจว่าผู้ป่วยปลอดภัยแล้วเท่านั้น',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('ยกเลิก'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('ปลอดภัยแล้ว'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    try {
      final res = await apiPatch('/api/alerts/${alert['id']}', body: {'resolved': true});
      if (!mounted) return;
      if (res.statusCode != 200) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('ปิดไม่สำเร็จ (${res.statusCode})')),
        );
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('ติดต่อเซิร์ฟเวอร์ไม่ได้')),
      );
    }
    _loadAlerts();
  }

  Widget _buildSosTile(Map<String, dynamic> alert) {
    final patient = alert['_patient'] as Map<String, dynamic>;
    final claimedByName = alert['claimed_by_name'] as String?;
    final createdAt = DateTime.tryParse(alert['created_at'] as String? ?? '')?.toLocal();

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      color: Colors.red[50],
      child: ListTile(
        leading: const Icon(Icons.emergency_share, color: Colors.red, size: 32),
        title: Text(
            alert['alert_type'] == 'sos_home'
                ? '${patient['name']} กด SOS ที่บ้าน'
                : '${patient['name']} กด SOS',
            style: const TextStyle(fontWeight: FontWeight.bold)),
        subtitle: Text([
          if (createdAt != null)
            '${createdAt.hour.toString().padLeft(2, '0')}:'
                '${createdAt.minute.toString().padLeft(2, '0')}',
          if (claimedByName != null) '$claimedByName กำลังไปรับ',
        ].join(' · ')),
        trailing: IconButton(
          icon: const Icon(Icons.check_circle_outline, color: Colors.green),
          tooltip: 'ยืนยันว่าปลอดภัยแล้ว',
          onPressed: () => _resolve(alert),
        ),
        onTap: () async {
          await pushAlertScreen(
            context,
            alert['id'] as int,
            (context) => SosAlertScreen(
              patientId: patient['patient_id'] as int,
              patientName: patient['name'] as String,
              alert: alert,
            ),
          );
          _loadAlerts();
        },
      ),
    );
  }

  /// Everything that isn't SOS: emergency/geofence/safe_zone_exit/gps_loss.
  /// These used to render inline in the family chat as a repeating red row
  /// per DB record; here they collapse into one card each, resolvable the
  /// same way as an SOS, but without SOS's "claim and go" flow — nobody is
  /// driving to a location for a risk-score alert.
  Widget _buildAlertTile(Map<String, dynamic> alert) {
    final patient = alert['_patient'] as Map<String, dynamic>;
    final createdAt = DateTime.tryParse(alert['created_at'] as String? ?? '')?.toLocal();
    final severity = alert['severity'] as String?;
    final critical = severity == 'critical' || severity == 'high';
    // Same claim a caregiver makes from SosAlertScreen ("I'll go get them")
    // reaches this row too — "emergency" is claimable the same way "sos" is,
    // it just isn't the type that takes over the screen on its own. Without
    // this a caregiver who already claimed it sees no sign of that here.
    final claimedByName = alert['claimed_by_name'] as String?;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      color: critical ? Colors.red[50] : Colors.orange[50],
      child: ListTile(
        leading: Icon(
          Icons.warning_amber_rounded,
          color: critical ? Colors.red : Colors.orange[800],
          size: 32,
        ),
        title: Text(
          '${patient['name']}: ${alert['message'] ?? alert['alert_type']}',
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        subtitle: Text([
          if (createdAt != null)
            '${createdAt.hour.toString().padLeft(2, '0')}:'
                '${createdAt.minute.toString().padLeft(2, '0')}',
          if (claimedByName != null) '$claimedByName กำลังไปรับ',
        ].join(' · ')),
        trailing: IconButton(
          icon: const Icon(Icons.check_circle_outline, color: Colors.green),
          tooltip: 'ยืนยันว่าปลอดภัยแล้ว',
          onPressed: () => _resolve(alert),
        ),
      ),
    );
  }

  /// trip_started/trip_arrived — a feed entry, not a problem. No resolve
  /// button (there's nothing to resolve) and a calmer colour than every other
  /// tile here, so a caregiver's eye still goes to an actual alert first.
  Widget _buildInfoTile(Map<String, dynamic> alert) {
    final patient = alert['_patient'] as Map<String, dynamic>;
    final createdAt = DateTime.tryParse(alert['created_at'] as String? ?? '')?.toLocal();
    final arrived = alert['alert_type'] == 'trip_arrived';

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      color: Colors.blue[50],
      child: ListTile(
        leading: Icon(
          arrived ? Icons.flag_rounded : Icons.directions_walk_rounded,
          color: Colors.blue[700],
          size: 32,
        ),
        title: Text(
          '${patient['name']}: ${alert['message'] ?? alert['alert_type']}',
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        subtitle: createdAt == null
            ? null
            : Text(
                '${createdAt.hour.toString().padLeft(2, '0')}:'
                '${createdAt.minute.toString().padLeft(2, '0')}',
              ),
      ),
    );
  }

  /// SOS keeps its own full-screen takeover and claim flow; trip lifecycle
  /// events are informational only; everything else gets the plainer
  /// resolvable card.
  Widget _buildAnyAlertTile(Map<String, dynamic> alert) {
    final type = alert['alert_type'];
    if (type == 'sos' || type == 'sos_home') return _buildSosTile(alert);
    if (informationalAlertTypes.contains(type)) return _buildInfoTile(alert);
    return _buildAlertTile(alert);
  }

  void _onRequestsChanged() {
    setState(() {});
  }

  Widget _buildTripRequestTile(TripRequest request) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${request.patientName} ขออนุญาตไป ${request.place['name']}',
              style: const TextStyle(fontWeight: FontWeight.bold),
            ),
            if (request.confidence != null)
              Text(
                'ความมั่นใจ: ${(request.confidence! * 100).toStringAsFixed(0)}%',
                style: const TextStyle(fontSize: 13, color: Colors.grey),
              ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: () => TripRequestDirectory.instance.decide(request, true),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.green,
                      foregroundColor: Colors.white,
                    ),
                    child: const Text('อนุญาต'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: ElevatedButton(
                    onPressed: () => TripRequestDirectory.instance.decide(request, false),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.red,
                      foregroundColor: Colors.white,
                    ),
                    child: const Text('ไม่อนุญาต'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final pending = TripRequestDirectory.instance.pending
      .where((r) => _myPatientIds.contains(r.patientId))
      .toList();
    final nothingAtAll =
        pending.isEmpty && _alerts.isEmpty && !_loadingAlerts;

    return Scaffold(
      appBar: AppBar(
        title: const Text('การแจ้งเตือน'),
      ),
      body: nothingAtAll
          ? const Center(child: Text('ไม่มีการแจ้งเตือน'))
          : RefreshIndicator(
              onRefresh: _loadAlerts,
              child: ListView(
                children: [
                  // Emergencies first. A trip request can wait for the length
                  // of a scroll; somebody who pressed SOS cannot.
                  ..._alerts.map(_buildAnyAlertTile),
                  ...pending.map(_buildTripRequestTile),
                ],
              ),
            ),
    );
  }
}
