import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'sos_contact_screen.dart';
import '../../services/sos_service.dart';
import '../../utils/bearing.dart';
import '../../services/directions_service.dart';

import 'dart:async';

class NavigationScreen extends StatefulWidget {
  final Map<String, dynamic> place;
  const NavigationScreen({super.key, required this.place});

  @override
  State<NavigationScreen> createState() {
    return _NavigationScreenState();
  }
}

class _NavigationScreenState extends State<NavigationScreen> {
  late final gmaps.LatLng _destination = gmaps.LatLng(widget.place['lat'], widget.place['lng']);
  StreamSubscription<Position>? _positionSubscription;
  LatLng? _currentLocation;
  List<gmaps.LatLng>? _routePoints;
  List<RouteStep>? _routeSteps;
  int _currentStepIndex = 0;
  gmaps.GoogleMapController? _mapController;
  double? _travelBearing;
  bool _sosSending = false;

  Future<void> _handleSOS() async {
    setState(() {
      _sosSending = true;
    });

    await triggerSOS();

    if (!mounted) return;
    setState((){
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

  static const double _stepAdvanceThresholdMeters = 15;

  Future<bool> _ensureLocationPermission() async {
    bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) return false;

    LocationPermission permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
  }
  return permission == LocationPermission.whileInUse ||
      permission == LocationPermission.always; 
  }

  @override
  void initState() {
    super.initState();
    _startLocationUpdates();
  }

  Future<void> _startLocationUpdates() async {
    final havePermission = await _ensureLocationPermission();
    if (!havePermission) return;

    _positionSubscription = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.best,
        distanceFilter: 10,
      ),
    ).listen((position) {
      final updated = LatLng(position.latitude, position.longitude);
      final isFirstFix = _currentLocation == null;
      final previous = _currentLocation;

      if (previous != null) {
        _travelBearing = calculateBearing(
        previous.latitude, previous.longitude,
        updated.latitude, updated.longitude,
     );
  }
  if (_routeSteps != null && _currentStepIndex < _routeSteps!.length - 1) {
    final stepEnd = _routeSteps![_currentStepIndex].endLocation;
    final stepEndLatLng = LatLng(stepEnd.latitude, stepEnd.longitude);
    final distanceToStepEnd = const Distance().as(LengthUnit.Meter, updated, stepEndLatLng);
    if (distanceToStepEnd < _stepAdvanceThresholdMeters) {
      _currentStepIndex++;
    }
  }

  setState(() {
    _currentLocation = updated;
  });

  _mapController?.animateCamera(
    gmaps.CameraUpdate.newCameraPosition(
      gmaps.CameraPosition(
        target: gmaps.LatLng(updated.latitude, updated.longitude),
        zoom: 17.5,
        bearing: _travelBearing ?? 0,
        tilt: 0,
      ),
    ),
  );

  if (isFirstFix) {
      _fetchRoute();
    }
  });
}

  Future<void> _fetchRoute() async {
    final origin = gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude);
    final route = await fetchRoute(origin, _destination);
    if (!mounted || route == null) return;
    setState(() {
      _routePoints = route.points;
      _routeSteps = route.steps;
      _currentStepIndex = 0;
    });
  }

  IconData _instructionIcon(String instruction) {
    switch (instruction) {
      case 'Turn left':
        return Icons.turn_left;
      case 'Turn right':
        return Icons.turn_right;
      case 'Turn around':
        return Icons.u_turn_left;
      case 'Go through the roundabout':
        return Icons.roundabout_left;
      case 'Arriving at destination':
        return Icons.flag;
      default:
        return Icons.straight;
    }
  }

  void _recenterOnPatient() {
    if (_currentLocation == null || _mapController == null) return;

    _mapController!.animateCamera(
      gmaps.CameraUpdate.newLatLngZoom(
        gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude),
        18.5,
      ),
    );
  }

  @override
  void dispose() {
    _positionSubscription?.cancel();
    super.dispose();
  }
  @override
  Widget build(BuildContext context) {

    final markers = <gmaps.Marker>{
      gmaps.Marker(
        markerId: const gmaps.MarkerId('destination'),
        position: _destination,
        icon: gmaps.BitmapDescriptor.defaultMarkerWithHue(gmaps.BitmapDescriptor.hueRed),
      ),
    };

    final polylines = <gmaps.Polyline>{
      if (_routePoints != null)
        gmaps.Polyline(
          polylineId: const gmaps.PolylineId('route'),
          points: _routePoints!,
          color: Colors.blue,
          width: 4,
        ),
    };

    String? instructionText;
    double? distanceToTurn;
    if (_routeSteps != null && _currentLocation != null && _currentStepIndex < _routeSteps!.length) {
      final currentStepEnd = _routeSteps![_currentStepIndex].endLocation;
      final currentStepEndLatLng = LatLng(currentStepEnd.latitude, currentStepEnd.longitude);
      distanceToTurn = const Distance().as(LengthUnit.Meter, _currentLocation!, currentStepEndLatLng);

      final nextIndex = _currentStepIndex + 1;
      instructionText = nextIndex < _routeSteps!.length
          ? _routeSteps![nextIndex].instruction
          : 'Arriving at destination';
    }

    return Scaffold(
      appBar: AppBar(
        title: Text(widget.place['name']),
      ),
      body: Stack(
        children: [
          gmaps.GoogleMap(
            initialCameraPosition: gmaps.CameraPosition(
              target: _destination,
              zoom: 15.0,
            ),
            markers: markers,
            polylines: polylines,
            onMapCreated: (controller) {
              _mapController = controller;
            },
          ),
          // Represents the patient. Fixed at screen-center rather than a
          // real Marker, since the camera already re-centers on the patient
          // and rotates to match travel direction on every GPS update — so
          // an icon that always points "up" on screen automatically shows
          // the direction they're currently heading.
          if (_currentLocation != null)
            const IgnorePointer(
              child: Center(
                child: Icon(Icons.navigation, color: Colors.blue, size: 48),
              ),
            ),
          if (instructionText != null)
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: SafeArea(
                child: Container(
                  margin: const EdgeInsets.all(12),
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                  decoration: BoxDecoration(
                    color: const Color.fromARGB(255, 50, 95, 68),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Row(
                    children: [
                      Icon(_instructionIcon(instructionText), color: Colors.white, size: 50),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            if (distanceToTurn != null)
                              Text(
                                '${distanceToTurn.toStringAsFixed(0)}m',
                                style: const TextStyle(color: Colors.white70, fontSize: 20),
                              ),
                            Text(
                              instructionText,
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 20,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            Positioned(
              bottom: 30,
              left: 0,
              right: 0,
              child: Center(
                child: SizedBox(
                  width: 96,
                  height: 96,
                  child: FloatingActionButton(
                    onPressed: _sosSending ? null : _handleSOS,
                    backgroundColor: Colors.red,
                    shape: const CircleBorder(),
                    child: const Text(
                      'SOS',
                      style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 18),
                    ),
                  )
                )
              )
            )
        ],
      ),
      floatingActionButtonLocation: FloatingActionButtonLocation.endFloat,
      floatingActionButton: FloatingActionButton(
        onPressed: _recenterOnPatient,
        child: const Icon(Icons.my_location),
      ),
    );
  }
}