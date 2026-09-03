import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'navigation_screen.dart';
import 'sos_contact_screen.dart';
import 'dart:async';
import 'package:uuid/uuid.dart';
import '../../services/places_service.dart';
import '../../services/sos_service.dart';
import '../../services/trip_approval_service.dart';
import '../../services/gps_reporter.dart';
import 'dart:convert';
import '../../services/api_client.dart';
import '../../services/session.dart';
import 'package:geolocator/geolocator.dart';
import '../../services/safe_zone_service.dart';
import '../login_screen.dart';

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

  Future<void> _handleLogout() async {
    await stopGpsReporting();
    await FirebaseAuth.instance.signOut();
    await Session.instance.clear();
    if (!mounted) return;
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (context) => const LoginScreen()),
      (route) => false,
    );
  }

  bool _sosSending = false;
  Future<void> _handleSOS() async {
    setState(() {
      _sosSending = true;
    });

    try {
      await triggerSOS();
    } catch (_) {}

    Map<String, dynamic>? safePlace;
    try {
      final position = await Geolocator.getCurrentPosition().timeout(const Duration(seconds: 5));
      safePlace = await findNearestSafePlace(position.latitude, position.longitude);
    } catch (_) {}

    if (!mounted) return;
    setState(() {
      _sosSending = false;
    });

    if (safePlace != null) {
      Navigator.push(
        context,
        MaterialPageRoute(builder: (context) => NavigationScreen(place: safePlace!)),
      );
    } else {
      Navigator.push(
        context,
        MaterialPageRoute(builder: (context) => const SosContactsScreen()),
      );
    }
  }

  Future<void> _loadRecommendations() async {
    final patientId = Session.instance.patientId;
    if (patientId == null) return;

    final response = await apiGet('/api/recommendation/$patientId');
    if (!mounted) return;

    if (response.statusCode != 200) {
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
  }

  Future<void> _requestTrip(Map<String, dynamic> place) async {
    // Level 1 patients never need caregiver approval (backend returns
    // status: "not_required" immediately) — this screen is Level 1 only, so
    // there is nothing to wait on and no reason to flash a "waiting" screen.
    setState(() {
      _selectedPlace = place;
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
          const Icon(Icons.hourglass_top, size: 120, color: Colors.blue),
          const SizedBox(height: 24),
          Text(
            'Asking your caregiver about ${_selectedPlace?['name'] ?? 'this trip'}...',
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
          const Icon(Icons.info_outline, size: 120, color: Colors.orange),
          const SizedBox(height: 24),
          const Text(
            'Let\'s pick something else',
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
              backgroundColor: Colors.blue,
              minimumSize: const Size(200, 56),
            ),
            child: const Text('OK', style: TextStyle(color: Colors.white, fontSize: 18)),
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
            const SnackBar(content: Text('Could not load that place, try again')),
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
    return Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(
                   'Hello!\n${widget.patientName ?? "Friend"}',
                   style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.bold),
                ),
                const Spacer(),
                const Icon(Icons.directions_walk, size: 32),
              ],
            ),
            const SizedBox(height: 8),
            Text (
              'Choose your destination',
              style: TextStyle(fontSize: 14, color: Colors.grey[800]),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _searchController,
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
                prefixIcon: const Icon(Icons.search),
                hintText: 'Search',
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(100)),
              ),
            ),
            const SizedBox(height: 16),
            
            Expanded(
              child: _predictions.isNotEmpty
                  ? ListView.builder(
                      itemCount: _predictions.length,
                      itemBuilder: (context, index) => _buildPredictionTile(_predictions[index]),
                    )
                  : _loadingPlaces
                      ? const Center(child: CircularProgressIndicator())
                      : filteredPlaces.isEmpty
                      ? const Center(
                          child: Text(
                            'No places found',
                            style: TextStyle(fontSize: 16, color: Colors.grey),
                          ),
                        )
                      : Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('Places you may like:', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                            const SizedBox(height: 2),
                            Expanded(
                              child: ListView.builder(
                                itemCount: filteredPlaces.length,
                                itemBuilder: (context, index) {
                                  final place = filteredPlaces[index];
                                  return Card(
                                    color: Colors.grey[200],
                                    margin: const EdgeInsets.symmetric(vertical: 6),
                                    child: ListTile(
                                      title: Text(place['name']),
                                      subtitle: const Text('Often visited'),
                                      trailing: ElevatedButton(
                                        onPressed: () => _requestTrip(place),
                                        child: const Text('Start'),
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
        title: const Text('Patient Home'),
        actions: [
          IconButton(
            onPressed: _handleLogout,
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: content,
      floatingActionButtonLocation: FloatingActionButtonLocation.centerFloat,
      floatingActionButton: SizedBox(
        width: 80,
        height: 80,
        child: FloatingActionButton(
          onPressed: _sosSending ? null : _handleSOS,
          backgroundColor: Colors.red,
          shape: const CircleBorder(),
          child: const Text(
          'SOS',
          style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 18),
        ),
      ),
    ),
    );
  }
}