import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';
import 'package:geolocator/geolocator.dart';
import 'package:flutter_compass/flutter_compass.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'dart:async';
import '../../utils/bearing.dart';
import '../../services/sos_service.dart';
import '../../services/directions_service.dart';

class NavigationScreen extends StatefulWidget{
  final Map<String, dynamic> place;
  const NavigationScreen({super.key, required this.place});
  
  @override 
  State<NavigationScreen> createState() => _NavigationScreenState();

}

class _NavigationScreenState extends State<NavigationScreen>{
  StreamSubscription<Position>? _positionSubscription;
  StreamSubscription<CompassEvent>? _compassSubscription;
  double? _heading;
  LatLng? _currentLocation;
  List<LatLng>? _routePoints;
  bool _sosSending = false;
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
    _startCompassUpdates();
  }

  Future<void> _startLocationUpdates() async {
    final hasPermission = await _ensureLocationPermission();
    if (!hasPermission) return;

    _positionSubscription = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 5, // meters — only fire when moved at least this far
      ),
    ).listen((Position position) {
      final updated = LatLng(position.latitude, position.longitude);
      final isFirstFix = _currentLocation == null;

      setState(() {
        _currentLocation = updated;
      });

      if (isFirstFix) {
        _fetchRoute();
      }
    });
  }

  Future<void> _fetchRoute() async {
    final origin = gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude);
    final destination = gmaps.LatLng(widget.place['lat'], widget.place['lng']);
    final route = await fetchRoute(origin, destination);
    if (!mounted || route == null) return;
    setState(() {
      _routePoints = route.points.map((p) => LatLng(p.latitude, p.longitude)).toList();
    });
  }

  /// Finds the point [lookaheadMeters] ahead of [current] along [route] —
  /// this is what the arrow points at instead of the raw destination, so it
  /// follows the actual street shape instead of cutting through buildings.
  LatLng _lookaheadTarget(List<LatLng> route, LatLng current, {double lookaheadMeters = 15}) {
    const distance = Distance();

    var nearestIndex = 0;
    var nearestDistance = double.infinity;
    for (var i = 0; i < route.length; i++) {
      final d = distance.as(LengthUnit.Meter, current, route[i]);
      if (d < nearestDistance) {
        nearestDistance = d;
        nearestIndex = i;
      }
    }

    var accumulated = 0.0;
    for (var i = nearestIndex; i < route.length - 1; i++) {
      accumulated += distance.as(LengthUnit.Meter, route[i], route[i + 1]);
      if (accumulated >= lookaheadMeters) {
        return route[i + 1];
      }
    }
    return route.last;
  }

  void _startCompassUpdates() {
    _compassSubscription = FlutterCompass.events?.listen((CompassEvent event) {
      final rawHeading = event.heading;
      if (rawHeading == null) return;

      setState(() {
        if (_heading == null) {
          _heading = rawHeading; // first reading — nothing to smooth against yet
        } else {
          const smoothingFactor = 0.15; // lower = smoother but slower to respond
          final delta = shortestAngleDelta(_heading!, rawHeading);
          _heading = (_heading! + delta * smoothingFactor) % 360;
          if (_heading! < 0) _heading = _heading! + 360;
        }
      });
    });
  }
  

  @override
  void dispose() {
  _positionSubscription?.cancel();
  _compassSubscription?.cancel();
  super.dispose();
  }
  
  Future<void> _handleSOS() async {
      setState(() {
        _sosSending = true;
      });

      await triggerSOS();

      if(!mounted) return;
      setState(() {
        _sosSending = false;
      });
      showDialog(
        context: context,
        builder: (context) => AlertDialog(
          icon: const Icon(Icons.check_circle, color: Colors.green, size: 128),
          title: const Text('Alert Sent', style: TextStyle(
            fontSize: 24,
            fontWeight: FontWeight.bold
          )),
          content: const Text('Your caregiver has been notified.', style: TextStyle(fontSize: 18)),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('OK')
            )
          ],
        ),
      );
    }
  /// Turns a signed relative angle (-180..180, degrees to turn from the
  /// current heading to face the destination) into a short instruction.
  /// Kept coarse on purpose — a moderate-stage patient needs one unambiguous
  /// word, not a precise bearing.
  String _directionText(double relativeAngle) {
    final magnitude = relativeAngle.abs();
    if (magnitude < 20) return 'Go straight ahead';
    if (magnitude >= 150) return 'Turn around';
    return relativeAngle > 0 ? 'Turn right' : 'Turn left';
  }

  @override
  Widget build(BuildContext context) {
    final destination = LatLng(widget.place['lat'], widget.place['lng']);

    double? distanceInMeters;
    double? rotationAngle;
    String? directionText;

    if (_currentLocation != null) {
      distanceInMeters = const Distance().as(LengthUnit.Meter, _currentLocation!, destination);

      if (_heading != null) {
        final bearingTarget = (_routePoints != null && _routePoints!.length >= 2)
            ? _lookaheadTarget(_routePoints!, _currentLocation!)
            : destination;

        final bearing = calculateBearing(
          _currentLocation!.latitude,
          _currentLocation!.longitude,
          bearingTarget.latitude,
          bearingTarget.longitude,
        );
        final relativeAngle = shortestAngleDelta(_heading!, bearing);
        rotationAngle = relativeAngle * (pi / 180);
        directionText = _directionText(relativeAngle);
      }
    }

    final arrived = distanceInMeters != null && distanceInMeters < 20;

    return Scaffold(
      appBar: AppBar(title: Text(widget.place['name'])),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(24.0),
            child: Text(
              'Going to: ${widget.place['name']}',
              style: const TextStyle(fontSize: 28, fontWeight: FontWeight.bold),
              textAlign: TextAlign.center,
            ),
          ),
          Expanded(
            child: Center(
              child: arrived
                  ? const Icon(Icons.check_circle, color: Colors.green, size: 120)
                  : (rotationAngle != null
                      ? Transform.rotate(
                          angle: rotationAngle,
                          child: const Icon(Icons.navigation, color: Colors.blue, size: 180),
                        )
                      : const CircularProgressIndicator()),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16.0),
            child: Text(
              arrived ? "You've arrived!" : (directionText ?? "Keep going"),
              style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w600),
            ),
          ),
          Padding(
            padding: const EdgeInsets.only(bottom: 30.0),
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
              ),
            ),
          ),
        ],
      ),
    );
  }
}