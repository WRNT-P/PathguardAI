import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'navigation_screen.dart';
import 'dart:async';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';
import '../../services/places_service.dart';
import '../../services/sos_service.dart';
import '../../services/trip_approval_service.dart';
import '../../services/gps_reporter.dart';
import 'dart:convert';
import '../../services/api_client.dart';
import '../../services/session.dart';
import '../../services/trip_request_directory.dart';
import '../login_screen.dart';
import '../../theme/patient_theme.dart';

enum _ScreenState { browsing, waitingApproval, rejected }

class PatientHomePageScreen extends StatefulWidget {
  final String? patientName;
  const PatientHomePageScreen({super.key, this.patientName});

  @override
  State<PatientHomePageScreen> createState() => _PatientHomePageScreenState();
}

class _PatientHomePageScreenState extends State<PatientHomePageScreen> {
  List<Map<String, dynamic>> recommendedPlaces = [];
  bool _loadingPlaces = true;

  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';
  Timer? _debounce;
  String? _sessionToken;
  List<PlacePrediction> _predictions = [];

  _ScreenState _state = _ScreenState.browsing;
  Map<String, dynamic>? _selectedPlace;
  bool _startingTrip = false;

  @override
  void initState() {
    super.initState();
    startGpsReporting();
    _loadRecommendations();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  bool _sosSending = false;

  /// Tell the caregiver, and nothing else.
  ///
  /// No safe-place lookup and no navigation: a patient who presses this is at
  /// home, not mid-walk, and marching them off to a police station is the
  /// wrong answer to "something is wrong here". Walking them somewhere safe
  /// belongs to the SOS button on the navigation screen, where they are
  /// already out and already moving.
  Future<void> _handleSOS() async {
    setState(() => _sosSending = true);

    var sent = false;
    try {
      sent = await triggerSOS(atHome: true);
    } catch (_) {
      sent = false;
    }

    if (!mounted) return;
    setState(() => _sosSending = false);

    showDialog(
      context: context,
      builder: (dialogContext) => AlertDialog(
        icon: Icon(sent ? Icons.check_circle : Icons.error_outline,
            color: sent ? Colors.green : Colors.red, size: 64),
        title: Text(sent ? 'กำลังมีคนมาช่วย' : 'ส่งไม่สำเร็จ',
            style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
        content: Text(
          sent
              ? 'แจ้งผู้ดูแลแล้ว รออยู่ตรงนี้นะ'
              : 'ติดต่อผู้ดูแลไม่ได้ กรุณาลองอีกครั้ง',
          style: const TextStyle(fontSize: 18),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: const Text('ตกลง', style: TextStyle(fontSize: 18)),
          ),
        ],
      ),
    );
  }

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
    if (patientId == null) {
      setState(() => _loadingPlaces = false);
      return;
    }

    try {
      final response = await _getWithRetry('/api/recommendation/$patientId');
      if (!mounted) return;

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
                  'confidence_pct': r['confidence_pct'],
                })
            .toList();
        _loadingPlaces = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loadingPlaces = false);
    }
  }

  Future<void> _requestTrip(Map<String, dynamic> place) async {
    // Level 1 patients never need caregiver approval (backend returns
    // status: "not_required" immediately) — this screen is Level 1 only, so
    // there is nothing to wait on and no reason to flash a "waiting" screen.
    //
    // It still costs a round trip through the tunnel, though, and nothing on
    // screen used to change while it ran: the patient pressed Start, saw
    // nothing happen, and pressed it again. The spinner below is the whole
    // acknowledgement they get, so it has to appear on the press itself.
    setState(() {
      _selectedPlace = place;
      _startingTrip = true;
    });

    bool approved = false;
    try {
      approved = await requestTripApproval(
        patientName: widget.patientName ?? 'patient',
        place: place,
      );
    } catch (_) {
      // Falls through to clearing the spinner. Letting this throw would leave
      // every Start button disabled with no way back.
    }

    if (!mounted) return;
    setState(() => _startingTrip = false);

    if (approved) {
      Navigator.push(
        context,
        MaterialPageRoute(builder: (context) => NavigationScreen(place: place)),
      ).then((_) {
        if (!mounted) return;
        setState(() {
          _state = _ScreenState.browsing;
          _selectedPlace = null;
        });
      });
    } else {
      setState(() {
        _state = _ScreenState.rejected;
      });
    }
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
                _state = _ScreenState.browsing;
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

  Widget _buildPredictionTile(PlacePrediction prediction) {
    return ListTile(
      leading: const Icon(Icons.location_on_outlined),
      title: Text(prediction.description),
      onTap: () async {
        final details = await fetchPlaceDetails(prediction.placeId, _sessionToken!);
        _sessionToken = null;

        if (!mounted) return;

        if (details == null) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('โหลดสถานที่นี้ไม่สำเร็จ ลองอีกครั้ง')),
          );
          return;
        }

        _requestTrip(details);
      },
    );
  }



  Widget _buildBrowsingState() {
    final filteredPlaces = recommendedPlaces.where((place) => place['name']
    .toString()
    .toLowerCase()
    .contains(_searchQuery.toLowerCase()))
    .toList();
    return Container(
        // Soft lavender-to-white backdrop for the whole home screen — the
        // one calm accent surface this screen gets, kept behind the content
        // so the search field and place cards stay the highest-contrast
        // things in view.
        decoration: BoxDecoration(gradient: PatientColors.lavenderCardGradient()),
        child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                     'สวัสดี!\n${widget.patientName ?? "เพื่อน"}',
                     style: const TextStyle(
                      fontSize: 26,
                      fontWeight: FontWeight.bold,
                      color: PatientColors.charcoal,
                      height: 1.2),
                  ),
                ),
                const Icon(Icons.directions_walk, size: 40, color: PatientColors.berry),
              ],
            ),
            const SizedBox(height: 8),
            Text (
              'เลือกที่ที่อยากไป',
              style: TextStyle(fontSize: 17, color: Colors.grey[800]),
            ),
            const SizedBox(height: 16),
            Semantics(
              textField: true,
              label: 'ค้นหาสถานที่ที่จะไป',
              child: TextField(
              controller: _searchController,
              style: const TextStyle(fontSize: 18),
              onChanged: (value) {
                setState(() {
                  _searchQuery = value;
                });

                if (value.isEmpty) {
                  _debounce?.cancel();
                  _sessionToken = null;
                  setState(() {
                    _predictions = [];
                  });
                  return;
                }

                _sessionToken ??= const Uuid().v4();
                _debounce?.cancel();
                _debounce = Timer(const Duration(milliseconds: 450), () async {
                  if (value.trim().length >= 2) {
                    final results = await fetchAutocomplete(value.trim(), _sessionToken!);
                    if (!mounted) return;
                    setState(() {
                      _predictions = results;
                    });
                  }
                });
              },
              decoration: InputDecoration(
                filled: true,
                fillColor: Colors.white,
                prefixIcon: const Icon(Icons.search, size: 26),
                hintText: 'ค้นหา',
                contentPadding: const EdgeInsets.symmetric(vertical: 16),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(100),
                  borderSide: BorderSide.none,
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(100),
                  borderSide: const BorderSide(color: PatientColors.berry, width: 2),
                ),
              ),
            ),
            ),
            const SizedBox(height: 16),
            
            Expanded(
              child: _predictions.isNotEmpty
                  ? ListView.builder(
                      // Keeps the last search result clear of the centered
                      // SOS FAB, which otherwise sits on top of it and can
                      // steal the tap.
                      padding: const EdgeInsets.only(bottom: 110),
                      itemCount: _predictions.length,
                      itemBuilder: (context, index) => _buildPredictionTile(_predictions[index]),
                    )
                  : _loadingPlaces
                      ? const Center(child: CircularProgressIndicator())
                      : filteredPlaces.isEmpty
                      ? const Center(
                          child: Text(
                            'ไม่พบสถานที่',
                            style: TextStyle(fontSize: 16, color: Colors.grey),
                          ),
                        )
                      : Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('สถานที่ที่คุณอาจชอบ:', style: TextStyle(fontSize: 17, fontWeight: FontWeight.bold, color: PatientColors.charcoal)),
                            const SizedBox(height: 4),
                            Expanded(
                              child: ListView.builder(
                                // Same reason as the predictions list above:
                                // clears the last card from underneath the
                                // centered SOS FAB.
                                padding: const EdgeInsets.only(bottom: 110),
                                itemCount: filteredPlaces.length,
                                itemBuilder: (context, index) {
                                  final place = filteredPlaces[index];
                                  return Card(
                                    color: Colors.white,
                                    elevation: 1,
                                    margin: const EdgeInsets.symmetric(vertical: 6),
                                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                                    child: Padding(
                                      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
                                      child: ListTile(
                                        title: Text(place['name'], style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w600)),
                                        subtitle: const Text('ไปบ่อย', style: TextStyle(fontSize: 14)),
                                        trailing: SizedBox(
                                          height: 48,
                                          child: ElevatedButton(
                                            onPressed: _startingTrip
                                                ? null
                                                : () => _requestTrip(place),
                                            style: ElevatedButton.styleFrom(
                                              backgroundColor: PatientColors.berry,
                                              foregroundColor: Colors.white,
                                              minimumSize: const Size(88, 48),
                                              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                                            ),
                                            child: _startingTrip && _selectedPlace == place
                                                ? const SizedBox(
                                                    width: 18,
                                                    height: 18,
                                                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                                                  )
                                                : const Text('เริ่ม', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                                          ),
                                        ),
                                      ),
                                    ),
                                  );
                                },
                              ),
                            ),
                          ],
                        ),
            )
          ],
        ),
      ),
      );
  }

  @override
  Widget build(BuildContext context) {
    final Widget content;
    switch (_state) {
      case _ScreenState.browsing:
        content = _buildBrowsingState();
        break;
      case _ScreenState.waitingApproval:
        content = _buildWaitingState();
        break;
      case _ScreenState.rejected:
        content = _buildRejectedState();
        break;
    }

    return Scaffold(
      appBar: AppBar(
        title: const Text('หน้าหลัก'),
        actions: [
          Semantics(
            button: true,
            label: 'ออกจากระบบ',
            child: IconButton(
              onPressed: _handleLogout,
              tooltip: 'ออกจากระบบ',
              icon: const Icon(Icons.logout),
            ),
          ),
        ],
      ),
      body: content,
      floatingActionButtonLocation: FloatingActionButtonLocation.centerFloat,
      // The single most important control on this screen — always the
      // biggest, reddest, least-buried thing in view, on purpose.
      floatingActionButton: Semantics(
        button: true,
        label: 'ปุ่มฉุกเฉิน SOS กดเพื่อแจ้งผู้ดูแลทันที',
        child: SizedBox(
          width: 88,
          height: 88,
          child: FloatingActionButton(
            onPressed: _sosSending ? null : _handleSOS,
            backgroundColor: PatientColors.danger,
            shape: const CircleBorder(),
            child: _sosSending
                ? const SizedBox(
                    width: 22,
                    height: 22,
                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                  )
                : const Text(
                    'SOS',
                    style: TextStyle(
                        color: Colors.white, fontWeight: FontWeight.bold, fontSize: 20),
                  ),
          ),
        ),
      ),
    );
  }
}