import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;
import 'navigation_screen.dart';
import '../../services/sos_service.dart';
import '../../services/trip_approval_service.dart';
import '../../services/gps_reporter.dart';
import 'dart:convert';
import '../../services/api_client.dart';
import '../../services/session.dart';
import '../../services/trip_request_directory.dart';
import '../login_screen.dart';
import '../../theme/patient_theme.dart';

enum _ScreenState { picking, waitingApproval, rejected, sosActive }

class PatientHomePageScreen extends StatefulWidget {
  final String? patientName;
  const PatientHomePageScreen({super.key, this.patientName});

  @override
  State<PatientHomePageScreen> createState() => _PatientHomePageScreenState();
}

class _PatientHomePageScreenState extends State<PatientHomePageScreen> {
  List<Map<String,dynamic>> recommendedPlaces = [];
  bool _loadingPlaces = true;

  /// A cold Cloudflare tunnel/backend can 502 or time out on the very first
  /// request after being idle (e.g. right after pairing on a fresh app
  /// launch) — same problem patient_login_screen.dart's _postWithRetry
  /// exists for. Without a retry here that transient failure looked
  /// identical to "caregiver hasn't added any places yet" even when places
  /// existed all along.
  Future<http.Response?> _getWithRetry(String path, {int attempts = 3}) async {
    for (var i = 0; i < attempts; i++) {
      try {
        final res = await apiGet(path);
        if (res.statusCode == 200) return res;
        if (i == attempts - 1) return res;
      } catch (_) {
        if (i == attempts - 1) return null;
      }
      await Future.delayed(Duration(seconds: 1 + i));
    }
    return null;
  }

  Future<void> _loadRecommendations() async {
    final patientId = Session.instance.patientId;
    if(patientId == null) {
      setState(() => _loadingPlaces = false);
      return;
    }

    try {
      final response = await _getWithRetry('/api/recommendation/$patientId');
      if(!mounted) return;

      if (response == null || response.statusCode != 200) {
        setState(() => _loadingPlaces = false);
        return;
      }

      final body = jsonDecode(response.body);
      final recommendations = body['recommendations'] as List;

      setState(() {
        recommendedPlaces = recommendations
            .where((r) => r['place_name'] != null)
            .map((r) => {
                  'name': r['place_name'] as String,
                  'lat': (r['latitude'] as num).toDouble(),
                  'lng': (r['longitude'] as num).toDouble(),
                })
            .toList();
        _loadingPlaces = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loadingPlaces = false);
    }
   }

  @override
  void initState() {
    super.initState();
    startGpsReporting();
    _loadRecommendations();
  }



  _ScreenState _state = _ScreenState.picking;
  Map<String, dynamic>? _selectedPlace;
  bool _sosSending = false;

  Future<void> _handleLogout() async {
    await stopGpsReporting();
    await FirebaseAuth.instance.signOut();
    // Drop the Firebase listeners with the session. The next account on
    // this device must not inherit the last one's rooms.
    TripRequestDirectory.instance.clear();
    await Session.instance.clear();
    if (!mounted) return;
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (context) => const LoginScreen()),
      (route) => false,
    );
  }

  @override
  void dispose() {
    super.dispose();
  }

  Future<void> _handleSelectPlace(Map<String, dynamic> place) async {
    setState(() {
      _selectedPlace = place;
      _state = _ScreenState.waitingApproval;
    });

    final approved = await requestTripApproval(
      patientName: widget.patientName ?? 'patient',
      place: place,
    );

    if (!mounted) return;

    if (approved) {
      Navigator.push(
        context,
        MaterialPageRoute(builder: (context) => NavigationScreen(place: place)),
      ).then((_) {
        if (!mounted) return;
        setState(() {
          _state = _ScreenState.picking;
          _selectedPlace = null;
        });
      });
    } else {
      setState(() {
        _state = _ScreenState.rejected;
      });
    }
  }

  Future<void> _handleSOS() async {
    setState(() {
      _sosSending = true;
    });

    try {
      await triggerSOS();
    } catch (_) {}

    if (!mounted) return;
    setState(() {
      _sosSending = false;
      _state = _ScreenState.sosActive;
    });
  }

  Widget _buildStayPutState() {
    return const Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.favorite, size: 120, color: Colors.red),
          SizedBox(height: 24),
          Text(
            'รออยู่ตรงนี้นะ',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 28, fontWeight: FontWeight.bold),
          ),
          SizedBox(height: 12),
          Text(
            'กำลังมีคนมาช่วย',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }

  Widget _buildSosButton() {
    // The single most important control on this screen — always the
    // biggest, reddest, least-buried thing in view, on purpose.
    return Semantics(
      button: true,
      label: 'ปุ่มฉุกเฉิน SOS กดเพื่อแจ้งผู้ดูแลทันที',
      child: SizedBox(
        width: 96,
        height: 96,
        child: FloatingActionButton(
          onPressed: _sosSending ? null : _handleSOS,
          backgroundColor: PatientColors.danger,
          shape: const CircleBorder(),
          child: const Text(
            'SOS',
            style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 18),
          ),
        ),
      ),
    );
  }

  Widget _buildWaitingState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.hourglass_top, size: 120, color: PatientColors.berry),
          const SizedBox(height: 24),
          Text(
            'กำลังถามผู้ดูแลเรื่อง ${_selectedPlace?['name'] ?? 'การเดินทางนี้'}...',
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }

  Widget _buildRejectedState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.info_outline, size: 120, color: PatientColors.charcoal),
          const SizedBox(height: 24),
          const Text(
            'ลองเลือกที่อื่นกันนะ',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 24, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 24),
          ElevatedButton(
            onPressed: () {
              setState(() {
                _state = _ScreenState.picking;
                _selectedPlace = null;
              });
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: PatientColors.berry,
              minimumSize: const Size(200, 60),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
            ),
            child: const Text('ตกลง', style: TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w700)),
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.hourglass_empty, size: 120, color: Colors.grey[500]),
          const SizedBox(height: 24),
          const Text(
            'รอผู้ดูแลเพิ่มสถานที่ให้',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }

  Widget _buildPlaceTile(Map<String, dynamic> place) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Semantics(
        button: true,
        label: 'ไป ${place['name']}',
        child: InkWell(
          onTap: () => _handleSelectPlace(place),
          borderRadius: BorderRadius.circular(16),
          child: Container(
            height: 120,
            width: double.infinity,
            decoration: BoxDecoration(
              gradient: PatientColors.lavenderCardGradient(),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: PatientColors.lavender, width: 1.5),
            ),
            child: Row(
              children: [
                const SizedBox(width: 20),
                const Icon(Icons.place, size: 48, color: PatientColors.berry),
                const SizedBox(width: 20),
                Expanded(
                  child: Text(
                    place['name'],
                    style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold, color: PatientColors.charcoal),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildPickingState() {
    if (_loadingPlaces) {
      return const Center(child: CircularProgressIndicator());
    }
    if (recommendedPlaces.isEmpty) {
      return _buildEmptyState();
    }
    // Column alone fit the 3-tile design this screen shipped with — a 4th
    // tile (home + 3 recommended, 2026-09-06) can run past the screen height
    // on shorter devices: a debug build flags that with the yellow/black
    // overflow stripes, but a release build just clips the overflow off the
    // bottom with no warning at all.
    return SingleChildScrollView(
      // Bottom padding equal to the SOS FAB's own footprint (96 tall + its
      // ~16 default margin) plus a little breathing room — without it the
      // centered floating SOS button sits directly on top of (and can
      // intercept taps meant for) the last place tile once the list is long
      // enough to reach the bottom of the screen.
      padding: const EdgeInsets.only(bottom: 132),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'เลือกที่ที่อยากไป',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 20),
          ...recommendedPlaces.map(_buildPlaceTile),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final Widget content;
    switch (_state) {
      case _ScreenState.picking:
        content = _buildPickingState();
        break;
      case _ScreenState.waitingApproval:
        content = _buildWaitingState();
        break;
      case _ScreenState.rejected:
        content = _buildRejectedState();
        break;
      case _ScreenState.sosActive:
        content = _buildStayPutState();
        break;
    }

    return Scaffold(
      appBar: AppBar(
        title: Text('สวัสดี ${widget.patientName ?? "เพื่อน"}'),
        actions: [
          IconButton(
            onPressed: _handleLogout,
            tooltip: 'ออกจากระบบ',
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: content,
      ),
      floatingActionButtonLocation: FloatingActionButtonLocation.centerFloat,
      floatingActionButton: _buildSosButton(),
    );
  }
}
