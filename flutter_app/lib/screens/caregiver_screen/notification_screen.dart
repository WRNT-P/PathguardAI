import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../services/trip_request_directory.dart';
import 'sos_alert_screen.dart';

class NotificationScreen extends StatefulWidget {
  const NotificationScreen({super.key});

  @override
  State<NotificationScreen> createState() => _NotificationScreenState();
}

class _NotificationScreenState extends State<NotificationScreen> {
  /// Unresolved SOS presses across every patient this caregiver looks after.
  ///
  /// An "sos" alert never closes itself — a person pressed the button, so a
  /// person decides when it is over — so it belongs on a list the caregiver
  /// can come back to, not only in a full-screen takeover they may have
  /// dismissed while driving.
  List<Map<String, dynamic>> _sosAlerts = [];
  bool _loadingAlerts = true;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    TripRequestDirectory.instance.addListener(_onRequestsChanged);
    _loadSosAlerts();
    _poll = Timer.periodic(const Duration(seconds: 20), (_) => _loadSosAlerts());
  }

  @override
  void dispose() {
    _poll?.cancel();
    TripRequestDirectory.instance.removeListener(_onRequestsChanged);
    super.dispose();
  }

  Future<void> _loadSosAlerts() async {
    try {
      final res = await apiGet('/api/patients');
      if (res.statusCode != 200) return;
      final patients = (jsonDecode(res.body)['patients'] as List)
          .cast<Map<String, dynamic>>();

      final found = <Map<String, dynamic>>[];
      for (final patient in patients) {
        final id = patient['patient_id'] as int;
        final alertsRes = await apiGet('/api/patients/$id/alerts');
        if (alertsRes.statusCode != 200) continue;
        final alerts = (jsonDecode(alertsRes.body)['alerts'] as List)
            .cast<Map<String, dynamic>>();
        for (final alert in alerts) {
          final type = alert['alert_type'];
          if (alert['resolved'] == false && (type == 'sos' || type == 'sos_home')) {
            found.add({...alert, '_patient': patient});
          }
        }
      }

      if (!mounted) return;
      setState(() {
        _sosAlerts = found;
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
        title: Text('Close ${patient['name']}\'s SOS?'),
        content: const Text(
          'This clears the alert for everyone. Only do it if you know the '
          'patient is safe.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('They are safe'),
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
          SnackBar(content: Text('Could not close it (${res.statusCode})')),
        );
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Could not reach the server')),
      );
    }
    _loadSosAlerts();
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
                ? '${patient['name']} pressed SOS at home'
                : '${patient['name']} pressed SOS',
            style: const TextStyle(fontWeight: FontWeight.bold)),
        subtitle: Text([
          if (createdAt != null)
            '${createdAt.hour.toString().padLeft(2, '0')}:'
                '${createdAt.minute.toString().padLeft(2, '0')}',
          if (claimedByName != null) '$claimedByName is on their way',
        ].join(' · ')),
        trailing: IconButton(
          icon: const Icon(Icons.check_circle_outline, color: Colors.green),
          tooltip: 'Mark as resolved',
          onPressed: () => _resolve(alert),
        ),
        onTap: () async {
          await Navigator.push(
            context,
            MaterialPageRoute(
              builder: (context) => SosAlertScreen(
                patientId: patient['patient_id'] as int,
                patientName: patient['name'] as String,
                alert: alert,
              ),
            ),
          );
          _loadSosAlerts();
        },
      ),
    );
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
              '${request.patientName} want to ask to go to ${request.place['name']}',
              style: const TextStyle(fontWeight: FontWeight.bold),
            ),
            if (request.confidence != null)
              Text(
                'Confidence: ${(request.confidence! * 100).toStringAsFixed(0)}%',
                style: const TextStyle(fontSize: 13, color: Colors.grey),
              ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: () => TripRequestDirectory.instance.decide(request.id, true),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.green,
                      foregroundColor: Colors.white,
                    ),
                    child: const Text('Approve'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: ElevatedButton(
                    onPressed: () => TripRequestDirectory.instance.decide(request.id, false),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.red,
                      foregroundColor: Colors.white,
                    ),
                    child: const Text('Reject'),
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
    final pending = TripRequestDirectory.instance.pending;
    final nothingAtAll =
        pending.isEmpty && _sosAlerts.isEmpty && !_loadingAlerts;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Notifications'),
      ),
      body: nothingAtAll
          ? const Center(child: Text('No notifications'))
          : RefreshIndicator(
              onRefresh: _loadSosAlerts,
              child: ListView(
                children: [
                  // Emergencies first. A trip request can wait for the length
                  // of a scroll; somebody who pressed SOS cannot.
                  ..._sosAlerts.map(_buildSosTile),
                  ...pending.map(_buildTripRequestTile),
                ],
              ),
            ),
    );
  }
}
