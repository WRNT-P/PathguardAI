import 'package:flutter/material.dart';
import 'navigation_screen.dart';
import 'sos_contact_screen.dart';
import 'dart:async';
import 'package:uuid/uuid.dart';
import '../../services/places_service.dart';
import '../../services/sos_service.dart';
import '../../services/trip_approval_service.dart';

enum _ScreenState { browsing, waitingApproval, rejected }

class PatientHomePageScreen extends StatefulWidget {
  final String? patientName;
  const PatientHomePageScreen({super.key, this.patientName});

  @override
  State<PatientHomePageScreen> createState() => _PatientHomePageScreenState();
}

class _PatientHomePageScreenState extends State<PatientHomePageScreen> {
  final List<Map<String, dynamic>> recommendedPlaces = const [
    {'name': 'Market', 'distanceKm': 2.0, 'temp': 35, 'lat': 13.7580, 'lng': 100.5040},
    {'name': 'Temple', 'distanceKm': 3.7, 'temp': 35, 'lat': 13.7600, 'lng': 100.5100},
    {'name': 'Department Store', 'distanceKm': 5.6, 'temp': 35, 'lat': 13.7650, 'lng': 100.5200},
  ];

  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';
  Timer? _debounce;
  String? _sessionToken;
  List<PlacePrediction> _predictions = [];

  _ScreenState _state = _ScreenState.browsing;
  Map<String, dynamic>? _selectedPlace;

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  bool _sosSending = false;
  Future<void> _handleSOS() async {
    setState(() {
      _sosSending = true;
    });

    await triggerSOS();

    if (!mounted) return;
    setState(() {
      _sosSending = false;
    });

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        icon: const Icon(Icons.check_circle, color: Colors.green, size: 64),
        title: const Text('Alert Sent', style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
        content: const Text('Your caregiver has been notified.', style: TextStyle(fontSize: 18)),
        actions: [
          TextButton(
            onPressed: () {
              Navigator.of(context).pop();
              Navigator.push(
                context,
                MaterialPageRoute(builder: (context) => const SosContactsScreen()),
              );
            },
            child: const Text('OK', style: TextStyle(fontSize: 18)),
          ),
        ],
      ),
    );
  }

  Future<void> _requestTrip(Map<String, dynamic> place) async {
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
                                      subtitle: Text('${place['distanceKm']} km · ${place['temp']}°C'),
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