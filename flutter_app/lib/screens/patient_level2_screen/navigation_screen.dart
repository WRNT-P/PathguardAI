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
  /// True once the magnetometer has actually produced a reading. Not every
  /// device has one — an emulator never does, and some budget handsets don't
  /// either — and until this flips we steer by the direction of travel
  /// instead. Without it the screen sat on a spinner forever, because
  /// ``_heading`` had exactly one writer and that writer never fired.
  bool _compassHasReported = false;
  /// Location was refused (or the service is off). Kept so the screen can say
  /// so: the old code just returned out of ``_startLocationUpdates`` and left
  /// the same spinner up, which is indistinguishable from "still loading" and
  /// tells a patient nothing they can act on.
  bool _locationUnavailable = false;

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
    if (!hasPermission) {
      if (mounted) setState(() => _locationUnavailable = true);
      return;
    }

    _positionSubscription = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 5, // meters — only fire when moved at least this far
      ),
    ).listen((Position position) {
      final updated = LatLng(position.latitude, position.longitude);
      final isFirstFix = _currentLocation == null;
      final previous = _currentLocation;

      setState(() {
        _currentLocation = updated;
        // Only when there is no compass: the magnetometer knows which way the
        // patient is FACING, which is the right question here, while this only
        // knows which way they last MOVED. They agree while walking forward
        // and disagree when someone stops and turns on the spot — so the
        // compass wins whenever it exists, and this keeps the screen usable
        // when it doesn't.
        if (!_compassHasReported && previous != null) {
          _updateHeading(calculateBearing(
            previous.latitude, previous.longitude,
            updated.latitude, updated.longitude,
          ));
        }
      });

      if (isFirstFix) {
        _fetchRoute();
      }
    });
  }

  /// Ease [rawHeading] into ``_heading`` instead of snapping to it.
  ///
  /// Shared by both sources on purpose. A GPS-derived bearing off 5 m steps is
  /// noisy enough that an unsmoothed arrow visibly jitters, and a moderate-stage
  /// patient reading a twitching arrow is being given a worse instruction than
  /// no arrow at all. Call inside a ``setState``.
  void _updateHeading(double rawHeading) {
    if (_heading == null) {
      _heading = rawHeading; // first reading — nothing to smooth against yet
      return;
    }
    const smoothingFactor = 0.15; // lower = smoother but slower to respond
    final delta = shortestAngleDelta(_heading!, rawHeading);
    _heading = (_heading! + delta * smoothingFactor) % 360;
    if (_heading! < 0) _heading = _heading! + 360;
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
    // ``FlutterCompass.events`` is null on a device with no magnetometer, and
    // on some that have one it is non-null but never emits. Neither case is an
    // error and neither used to be handled — the stream simply stayed quiet
    // and the screen waited on it forever.
    _compassSubscription = FlutterCompass.events?.listen((CompassEvent event) {
      final rawHeading = event.heading;
      if (rawHeading == null) return;

      setState(() {
        _compassHasReported = true;
        _updateHeading(rawHeading);
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

  /// The big centre graphic. Four states, and the spinner is now only one of
  /// them — it used to be the catch-all, which is why every failure looked
  /// like "still loading" and none of them ever stopped.
  Widget _indicator(bool arrived, double? rotationAngle) {
    if (arrived) {
      return const Icon(Icons.check_circle, color: Colors.green, size: 120);
    }
    if (_locationUnavailable) {
      return const Icon(Icons.location_off, color: Colors.orange, size: 120);
    }
    if (rotationAngle != null) {
      return Transform.rotate(
        angle: rotationAngle,
        child: const Icon(Icons.navigation, color: Colors.blue, size: 180),
      );
    }
    if (_currentLocation != null) {
      // Located, but no direction yet: no compass and not enough movement to
      // derive one. Show the arrow greyed and straight rather than a spinner —
      // the patient is not waiting on the app, the app is waiting on them, and
      // the caption below says so.
      return const Icon(Icons.navigation, color: Colors.grey, size: 180);
    }
    return const CircularProgressIndicator();
  }

  String _statusText(bool arrived, String? directionText) {
    if (arrived) return "You've arrived!";
    if (_locationUnavailable) return 'Turn on location to start';
    if (directionText != null) return directionText;
    if (_currentLocation != null) return 'Start walking to find your direction';
    return 'Finding your location…';
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
            child: Center(child: _indicator(arrived, rotationAngle)),
          ),
          Padding(
            padding: const EdgeInsets.all(16.0),
            child: Text(
              _statusText(arrived, directionText),
              textAlign: TextAlign.center,
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