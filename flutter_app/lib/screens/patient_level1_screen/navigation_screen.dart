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

  /// Every place the patient has actually stood, oldest first, thinned to one
  /// point per [_trailSpacingMeters].
  ///
  /// This is what "take me back the way I came" walks in reverse. It is
  /// deliberately the *walked* track and not a Directions route: the point of
  /// retracing is that the patient has already seen this ground once, and a
  /// shortest-path route home would send someone with early-stage dementia
  /// down a street they have never been on to save two minutes.
  ///
  /// Never trimmed from the front. The oldest entry is where they set out —
  /// dropping it to cap the list would throw away the one point the whole
  /// feature exists to reach.
  final List<LatLng> _trail = [];
  static const double _trailSpacingMeters = 15;

  /// True while retracing. The forward route's turn-by-turn is hidden then:
  /// those instructions describe a route we are no longer following, and a
  /// confident wrong instruction is worse than none.
  bool _backtracking = false;

  /// Index into [_trail] of the point currently being walked toward while
  /// backtracking. Counts down; 0 is where the walk began.
  int _backtrackIndex = 0;

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
  /// Below this, a bearing between two fixes is GPS noise rather than a turn.
  static const double _bearingMinMoveMeters = 5;

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
  }

  Future<void> _startLocationUpdates() async {
    final havePermission = await _ensureLocationPermission();
    if (!havePermission) {
      // Was a bare `return`. Without a position the blue arrow never appears,
      // the map never rotates and the trail never records — so both the
      // heading-up view and "take me back" quietly do nothing, and the screen
      // gives no hint why. Say it instead.
      if (mounted) setState(() => _locationUnavailable = true);
      return;
    }

    _positionSubscription = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.best,
        distanceFilter: 10,
      ),
    ).listen((position) {
      final updated = LatLng(position.latitude, position.longitude);
      final isFirstFix = _currentLocation == null;
      final previous = _currentLocation;
      const distance = Distance();

      setState(() {
        if (previous != null) {
          final moved = distance.as(LengthUnit.Meter, previous, updated);
          // A bearing computed across a few metres is mostly GPS noise, and
          // feeding it to the camera is what made the map swing while the
          // patient walked in a straight line. Below the floor the map simply
          // keeps the heading it had, which is the honest answer.
          if (moved >= _bearingMinMoveMeters) {
            _updateTravelBearing(calculateBearing(
              previous.latitude, previous.longitude,
              updated.latitude, updated.longitude,
            ));
          }
        }

        // Not while retracing. The trail is the record of the way *out*;
        // appending the way back would mean a second press of "Take me back"
        // retraces the retrace and walks the patient out again.
        if (!_backtracking &&
            (_trail.isEmpty ||
                distance.as(LengthUnit.Meter, _trail.last, updated) >= _trailSpacingMeters)) {
          _trail.add(updated);
        }

        if (_backtracking) {
          // Walk the recorded points off the end of the list. Arriving at one
          // means the next target is the one before it, and index 0 is where
          // the walk began.
          while (_backtrackIndex > 0 &&
              distance.as(LengthUnit.Meter, updated, _trail[_backtrackIndex]) <
                  _stepAdvanceThresholdMeters) {
            _backtrackIndex--;
          }
        } else if (_routeSteps != null && _currentStepIndex < _routeSteps!.length - 1) {
          final stepEnd = _routeSteps![_currentStepIndex].endLocation;
          final stepEndLatLng = LatLng(stepEnd.latitude, stepEnd.longitude);
          if (distance.as(LengthUnit.Meter, updated, stepEndLatLng) <
              _stepAdvanceThresholdMeters) {
            _currentStepIndex++;
          }
        }

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

  /// Ease [rawBearing] into [_travelBearing] instead of snapping to it.
  ///
  /// The camera reads this every fix, so an unsmoothed value rotates the whole
  /// map by whatever the last GPS jump happened to say. Smoothing costs a
  /// little lag on a real turn and buys a map that stops lurching.
  void _updateTravelBearing(double rawBearing) {
    if (_travelBearing == null) {
      _travelBearing = rawBearing;
      return;
    }
    const smoothingFactor = 0.25; // higher than the lv2 arrow: a map that lags
                                  // a turn is more disorienting than a nudge.
    final delta = shortestAngleDelta(_travelBearing!, rawBearing);
    _travelBearing = (_travelBearing! + delta * smoothingFactor) % 360;
    if (_travelBearing! < 0) _travelBearing = _travelBearing! + 360;
  }

  /// Start retracing, or stop and go back to heading for the destination.
  void _toggleBacktrack() {
    setState(() {
      _backtracking = !_backtracking;
      if (_backtracking) _backtrackIndex = _nearestTrailIndex();
    });
  }

  /// Where on the recorded trail the patient is standing right now.
  ///
  /// Not simply the end of the list. Retracing can be stopped halfway and
  /// started again, and by then the far end of the trail is somewhere the
  /// patient has already walked away from — resuming from it would point them
  /// back out. The nearest point is the honest answer from anywhere.
  int _nearestTrailIndex() {
    if (_currentLocation == null || _trail.isEmpty) return 0;
    const distance = Distance();
    var nearest = 0;
    var nearestMeters = double.infinity;
    for (var i = 0; i < _trail.length; i++) {
      final d = distance.as(LengthUnit.Meter, _currentLocation!, _trail[i]);
      if (d < nearestMeters) {
        nearestMeters = d;
        nearest = i;
      }
    }
    // Standing on it already: the thing to walk toward is the one before.
    if (nearest > 0 && nearestMeters < _stepAdvanceThresholdMeters) nearest--;
    return nearest;
  }

  /// Metres still to walk along the recorded trail to reach the start.
  double _distanceRemainingOnTrail() {
    if (_currentLocation == null || _trail.isEmpty) return 0;
    const distance = Distance();
    var total = distance.as(LengthUnit.Meter, _currentLocation!, _trail[_backtrackIndex]);
    for (var i = _backtrackIndex; i > 0; i--) {
      total += distance.as(LengthUnit.Meter, _trail[i], _trail[i - 1]);
    }
    return total;
  }

  Future<void> _fetchRoute() async {
    final origin = gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude);
    final route = await fetchRoute(origin, _destination);
    if (!mounted || route == null) return;
    setState(() {
      _routePoints = route.points;
      _routeSteps = route.steps;
      _currentStepIndex = 0;
      // Seed the map's rotation from the route's own first leg. Travel bearing
      // needs two fixes ten metres apart to exist, so until now the map sat
      // north-up for the opening stretch of the walk — the exact stretch where
      // someone is deciding which way to set off.
      if (_travelBearing == null && _currentLocation != null && route.points.length >= 2) {
        final ahead = route.points[1];
        _travelBearing = calculateBearing(
          _currentLocation!.latitude, _currentLocation!.longitude,
          ahead.latitude, ahead.longitude,
        );
      }
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
      case 'Retracing your steps':
        return Icons.u_turn_left;
      case 'Back where you started':
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

    // While retracing, the forward route is not the line being walked, so it
    // comes off the map entirely rather than sitting there as a second
    // suggestion competing with the one the patient is following.
    final polylines = <gmaps.Polyline>{
      if (_backtracking && _trail.length >= 2)
        gmaps.Polyline(
          polylineId: const gmaps.PolylineId('backtrack'),
          points: _trail
              .sublist(0, _backtrackIndex + 1)
              .map((p) => gmaps.LatLng(p.latitude, p.longitude))
              .toList(),
          color: Colors.deepOrange,
          width: 5,
        )
      else if (_routePoints != null)
        gmaps.Polyline(
          polylineId: const gmaps.PolylineId('route'),
          points: _routePoints!,
          color: Colors.blue,
          width: 4,
        ),
    };

    String? instructionText;
    double? distanceToTurn;
    if (_backtracking) {
      final remaining = _distanceRemainingOnTrail();
      // Without this the feature has no ending — it would sit on "0m,
      // retracing" once the patient is standing where they set out.
      final backAtStart = _backtrackIndex == 0 && remaining < _stepAdvanceThresholdMeters;
      instructionText = backAtStart ? 'Back where you started' : 'Retracing your steps';
      distanceToTurn = backAtStart ? null : remaining;
    } else if (_routeSteps != null && _currentLocation != null && _currentStepIndex < _routeSteps!.length) {
      final currentStepEnd = _routeSteps![_currentStepIndex].endLocation;
      final currentStepEndLatLng = LatLng(currentStepEnd.latitude, currentStepEnd.longitude);
      distanceToTurn = const Distance().as(LengthUnit.Meter, _currentLocation!, currentStepEndLatLng);

      final nextIndex = _currentStepIndex + 1;
      instructionText = nextIndex < _routeSteps!.length
          ? _routeSteps![nextIndex].instruction
          : 'Arriving at destination';
    }

    // Two points is the shortest thing that is a path rather than a dot. Below
    // that there is nothing to retrace and the button says so by being dead
    // rather than by producing a route to where the patient already stands.
    final canBacktrack = _trail.length >= 2;

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
          if (_locationUnavailable)
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: SafeArea(
                child: Container(
                  margin: const EdgeInsets.all(12),
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                  decoration: BoxDecoration(
                    color: Colors.orange.shade800,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Row(
                    children: [
                      Icon(Icons.location_off, color: Colors.white, size: 40),
                      SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          'Turn on location to start navigating',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 20,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
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
                    // Retracing is a different mode, not a different turn, and
                    // the banner carries that so a glance says which line on
                    // the map is the one being walked.
                    color: _backtracking
                        ? Colors.deepOrange.shade800
                        : const Color.fromARGB(255, 50, 95, 68),
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
      floatingActionButton: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          FloatingActionButton.extended(
            heroTag: 'backtrack',
            // Disabled rather than hidden: a control that appears partway
            // through a walk is a control nobody finds. Greyed out it can be
            // seen, pressed, and understood before it is needed.
            onPressed: canBacktrack ? _toggleBacktrack : null,
            backgroundColor: canBacktrack
                ? (_backtracking ? Colors.deepOrange : null)
                : Colors.grey.shade400,
            icon: Icon(_backtracking ? Icons.close : Icons.u_turn_left),
            label: Text(_backtracking ? 'Stop' : 'Take me back'),
          ),
          const SizedBox(height: 12),
          FloatingActionButton(
            heroTag: 'recenter',
            onPressed: _recenterOnPatient,
            child: const Icon(Icons.my_location),
          ),
        ],
      ),
    );
  }
}