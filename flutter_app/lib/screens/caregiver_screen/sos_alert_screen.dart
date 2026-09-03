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
  bool _acting = false;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _refresh();
    _refreshTimer = Timer.periodic(const Duration(seconds: 8), (_) => _refresh());
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
          const SnackBar(content: Text('มีคนรับเรื่องนี้ไปแล้ว')),
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
              _alert['message'] as String? ?? 'ผู้ป่วยต้องการความช่วยเหลือ',
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
          ),
          if (lat != null && lng != null)
            SizedBox(
              height: 220,
              child: gmaps.GoogleMap(
                initialCameraPosition: gmaps.CameraPosition(
                  target: gmaps.LatLng(lat, lng),
                  zoom: 16,
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
              child: Text('ผู้ดูแลใกล้ที่สุด', style: TextStyle(fontWeight: FontWeight.w600)),
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
                        child: const Text('ฉันจะไปรับ', style: TextStyle(color: Colors.white)),
                      )
                    : claimedBy == myId
                        ? ElevatedButton(
                            onPressed: _acting ? null : _cancelClaim,
                            style: ElevatedButton.styleFrom(minimumSize: const Size(0, 48)),
                            child: const Text('ยกเลิกการรับเรื่อง'),
                          )
                        : Container(
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                              color: Colors.green[100],
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Text(
                              '${claimedByName ?? "ผู้ดูแลคนอื่น"} กำลังไปรับแล้ว',
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
