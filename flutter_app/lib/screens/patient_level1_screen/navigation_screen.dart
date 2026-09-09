import 'package:flutter/material.dart';
import '../../theme/app_theme.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'sos_contact_screen.dart';
import '../../services/sos_service.dart';
import '../../services/safe_zone_service.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';
import '../../utils/bearing.dart';
import '../../services/directions_service.dart';
import 'dart:ui' as ui;

import 'dart:async';
import 'dart:convert';

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
  /// How far along `_routePoints` the patient has walked — see the trimming
  /// in `_handlePosition`. Reset whenever a new route is fetched.
  int _routeProgressIndex = 0;
  List<RouteStep>? _routeSteps;
  int _currentStepIndex = 0;
  gmaps.GoogleMapController? _mapController;
  gmaps.BitmapDescriptor? _navigationIcon;
  double? _travelBearing;
  bool _sosSending = false;

  final List<LatLng> _trail = [];
  static const double _trailSpacingMeters = 15;

  /// North-up, or turned the way they are walking. Same control the level 2
  /// screen carries: a map that rotates is easier to walk by, and a map that
  /// stays north-up is easier to read against street signs — which one helps
  /// is the patient's answer, not ours.
  bool _northUp = true;

  bool _backtracking = false;

  int _backtrackIndex = 0;

  Future<void> _handleSOS() async {
    setState(() {
      _sosSending = true;
    });

    // Looking up the nearest caregiver's name does not block anything, so it
    // runs alongside. The safe place does block the SOS, because the alert
    // carries where the patient is being walked to — a caregiver who knows
    // that can meet them, and the coordinates in the alert are stale the
    // moment this screen starts moving them.
    String? nearestName;
    final nameLookup = () async {
      try {
        final patientId = Session.instance.patientId;
        if (patientId == null) return;
        final res = await apiGet('/api/patients/$patientId/caregivers');
        if (res.statusCode != 200) return;
        final caregivers =
            (jsonDecode(res.body)['caregivers'] as List).cast<Map<String, dynamic>>();
        if (caregivers.isNotEmpty) nearestName = caregivers.first['name'] as String?;
      } catch (_) {}
    }();

    final placeLookup = () async {
      try {
        // Already-tracked live location is near-instant; only ask the GPS
        // for a fresh fix if navigation hasn't produced one yet.
        var here = _currentLocation;
        if (here == null) {
          final pos = await Geolocator.getCurrentPosition(
            locationSettings: const LocationSettings(accuracy: LocationAccuracy.medium),
          ).timeout(const Duration(seconds: 3));
          here = LatLng(pos.latitude, pos.longitude);
        }
        return await findNearestSafePlace(here.latitude, here.longitude);
      } catch (_) {
        return null;
      }
    }();

    // Capped: telling the family is the part that must not wait on Google.
    // Past three seconds the SOS goes without a destination, and the walk
    // still starts when the lookup lands.
    final placeForAlert = await placeLookup
        .timeout(const Duration(seconds: 3), onTimeout: () => null);
    await triggerSOS(destinationName: placeForAlert?['name'] as String?)
        .catchError((_) => false);

    final safePlace = placeForAlert ?? await placeLookup.catchError((_) => null);
    await nameLookup;

    if (!mounted) return;
    setState((){
      _sosSending = false;
    });

    // A patient already mid-walk who presses SOS needs redirecting to safety,
    // not a dialog about who was told — this is exactly the "Safe Zone
    // Navigation" feature, just reached from a different screen than the
    // homepage's SOS button. Whatever destination they were headed to gets
    // replaced: it's no longer the point once SOS has been pressed.
    if (safePlace != null) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (context) => NavigationScreen(place: safePlace)),
      );
      return;
    }

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        icon: const Icon(Icons.check_circle, color: Colors.green, size: 64),
        title: const Text('Alert Sent', style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
        content: Text(
          nearestName != null
              ? '$nearestName is your nearest caregiver and has been notified.'
              : 'Your caregiver has been notified.',
          style: const TextStyle(fontSize: 18),
        ),
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
    _loadNavigationIcon();
  }

  Future<void> _startLocationUpdates() async {
    final havePermission = await _ensureLocationPermission();
    if (!havePermission) {
      if (mounted) setState(() => _locationUnavailable = true);
      return;
    }

    // The cached fix is what makes the map appear at once. Waiting on a fresh
    // high-accuracy one first cost up to five seconds of blank screen after
    // "Start" — and the route can only be fetched once a position exists, so
    // that delay was in front of the Directions call too, not beside it. The
    // stream below replaces this with a live fix within seconds either way.
    try {
      final cached = await Geolocator.getLastKnownPosition();
      if (cached != null) _handlePosition(cached);
    } catch (_) {}

    try {
      final seed = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      ).timeout(const Duration(seconds: 5));
      _handlePosition(seed);
    } catch (_) {}

    _positionSubscription = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.best,
        distanceFilter: 10,
      ),
    ).listen(_handlePosition);
  }

  Future<void> _loadNavigationIcon() async {
    const double size = 96;

    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder, const Rect.fromLTWH(0, 0, size, size));
    const center = Offset(size / 2, size / 2);

    final painter = TextPainter(textDirection: TextDirection.ltr)
      ..text = TextSpan(
        text: String.fromCharCode(Icons.navigation.codePoint),
        style: TextStyle(
          fontSize: size * 0.75,
          fontFamily: Icons.navigation.fontFamily,
          package: Icons.navigation.fontPackage,
          color: AppColors.primary,
        ),
      )
      ..layout();

    painter.paint(
      canvas,
      center - Offset(painter.width / 2, painter.height / 2),
    );

    final picture = recorder.endRecording();
    final image = await picture.toImage(size.toInt(), size.toInt());
    final byteData = await image.toByteData(format: ui.ImageByteFormat.png);

    final icon = gmaps.BitmapDescriptor.bytes(
      byteData!.buffer.asUint8List(),
      width: 48,
      height: 48,
    );

    if (mounted) setState(() => _navigationIcon = icon);
  }

  void _handlePosition(Position position) {
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

      // Walk the route line past the points already behind them. The route is
      // fetched once and never re-fetched (Directions is billed per call), so
      // without this the drawn line stays pinned to wherever the walk started
      // and the patient watches a path they are no longer on.
      //
      // Forward-only, and only while the next point is genuinely nearer than
      // the current one, so a route that doubles back near itself cannot snap
      // the line onto the wrong leg.
      final route = _routePoints;
      if (route != null) {
        while (_routeProgressIndex < route.length - 1) {
          final here = route[_routeProgressIndex];
          final next = route[_routeProgressIndex + 1];
          final toHere = distance.as(
              LengthUnit.Meter, updated, LatLng(here.latitude, here.longitude));
          final toNext = distance.as(
              LengthUnit.Meter, updated, LatLng(next.latitude, next.longitude));
          if (toNext >= toHere) break;
          _routeProgressIndex++;
        }
      }

      _currentLocation = updated;
    });

    _updateCamera();

    if (isFirstFix) {
      _fetchRoute();
    }
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
    final wasBacktracking = _backtracking;
    setState(() {
      _backtracking = !_backtracking;
      if (_backtracking) _backtrackIndex = _nearestTrailIndex();
    });
    if (wasBacktracking) _fetchRoute();
  }

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

  /// The part of the route still ahead, drawn from where the patient is now.
  ///
  /// Anchoring it to the live position is what makes the line follow them:
  /// the route itself is fetched once (Directions is billed per call), so the
  /// stored points never move, and drawing them raw left the line starting at
  /// wherever the walk began no matter how far along they were.
  List<gmaps.LatLng> _remainingRoute() {
    final route = _routePoints!;
    final ahead = route.sublist(_routeProgressIndex.clamp(0, route.length - 1));
    final here = _currentLocation;
    if (here == null) return ahead;
    return [gmaps.LatLng(here.latitude, here.longitude), ...ahead];
  }

  Future<void> _fetchRoute() async {
    final origin = gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude);
    final route = await fetchRoute(origin, _destination);
    if (!mounted) return;
    if (route == null) {
      // Directions can fail (quota, no walking route found, dropped
      // connection) — this used to leave the map with no line at all, which
      // for a patient trying to find their way is worse than an imperfect
      // one. A straight line at least says which direction to head.
      setState(() {
        _routePoints = [origin, _destination];
        _routeSteps = [];
        _currentStepIndex = 0;
        _routeProgressIndex = 0;
      });
      return;
    }
    setState(() {
      _routePoints = route.points;
      _routeSteps = route.steps;
      _currentStepIndex = 0;
      _routeProgressIndex = 0;
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

  /// Point the camera at the patient with whichever bearing [_northUp] calls
  /// for. Its own method so the toggle can apply immediately instead of
  /// waiting for the next GPS fix to move the camera.
  void _updateCamera({double zoom = 18.5}) {
    final current = _currentLocation;
    if (current == null) return;
    // newLatLngZoom cannot carry a bearing — newCameraPosition is the one
    // that keeps the rotation instead of snapping back to north-up.
    _mapController?.animateCamera(
      gmaps.CameraUpdate.newCameraPosition(
        gmaps.CameraPosition(
          target: gmaps.LatLng(current.latitude, current.longitude),
          zoom: zoom,
          bearing: _northUp ? 0 : (_travelBearing ?? 0),
          // The button is the camera-angle control: one press swaps the whole
          // view between tilted-and-turned (easier to walk by, the road ahead
          // fills the screen) and flat north-up (easier to read against a
          // street sign, and the only view that means anything to somebody
          // who navigates by knowing where north is).
          tilt: _northUp ? 0 : 60,
        ),
      ),
    );
  }

  void _toggleNorthUp() {
    setState(() => _northUp = !_northUp);
    _updateCamera();
  }

  void _recenterOnPatient() => _updateCamera();

  void _showDirectionsList() {
    showModalBottomSheet(
      context: context,
      builder: (context) => ListView.builder(
        itemCount: _routeSteps!.length,
        itemBuilder: (context, index) {
          final step = _routeSteps![index];
          return ListTile(
            leading: Icon(_instructionIcon(step.instruction)),
            title: Text(step.instruction),
            trailing: Text('${step.distanceMeters.toStringAsFixed(0)}m'),
          );
        },
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

      if (_currentLocation != null && _navigationIcon != null)
        gmaps.Marker(
          markerId: const gmaps.MarkerId('patient'),
          position: gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude),
          icon: _navigationIcon!,
          anchor: const Offset(0.5, 0.5), 
          flat: true,                     
          rotation: _travelBearing ?? 0,  
          zIndex: 1,
        )
    };
    final polylines = <gmaps.Polyline>{
      if (_backtracking && _trail.length >= 2)
        gmaps.Polyline(
          polylineId: const gmaps.PolylineId('backtrack'),
          points: [
            gmaps.LatLng(_currentLocation!.latitude,
            _currentLocation!.longitude),
            ..._trail
              .sublist(0, _backtrackIndex + 1)
              .map((p)=>gmaps.LatLng(p.latitude, p.longitude)),
          ],
          color: Colors.deepOrange,
          width: 5,
        )
      else if (_routePoints != null)
        gmaps.Polyline(
          polylineId: const gmaps.PolylineId('route'),
          points: _remainingRoute(),
          color: AppColors.primary,
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
        actions: [
          IconButton(
            icon: const Icon(Icons.list),
            onPressed: (_routeSteps == null || _routeSteps!.isEmpty)
              ? null
              : _showDirectionsList,
          ),
        ]
      ),
      body: Stack(
        children: [
          gmaps.GoogleMap(
            initialCameraPosition: gmaps.CameraPosition(
              target: _destination,
              zoom: 17.5,
            ),
            // Chase-camera only: pushes the camera's centre — where the
            // marker sits — down the screen, so what is ahead fills the view
            // instead of the ground already walked. North-up is a map being
            // read rather than followed, and a map reads from its middle.
            padding: EdgeInsets.only(
                top: _northUp ? 0 : MediaQuery.of(context).size.height * 0.35),
            markers: markers,
            polylines: polylines,
            onMapCreated: (controller) {
              _mapController = controller;
            },
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
              top: 16,
              left: 16,
              child: SizedBox(
                width: 48,
                height: 48,
                child: FloatingActionButton(
                  heroTag: 'northUpToggle',
                  tooltip: _northUp ? 'Switch to direction-up' : 'Switch to north-up',
                  backgroundColor: _northUp ? Colors.white : AppColors.primary,
                  onPressed: _toggleNorthUp,
                  child: Icon(Icons.explore,
                      color: _northUp ? AppColors.primary : Colors.white),
                ),
              ),
            ),
            // Bottom left, so it balances the controls on the right without
            // reaching the SOS circle in the middle. Ringing a person the
            // patient knows is a different kind of help from the red button —
            // quieter, and sometimes all they actually want.
            Positioned(
              bottom: 30,
              left: 16,
              child: FloatingActionButton.extended(
                heroTag: 'contacts',
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (context) => const SosContactsScreen()),
                ),
                backgroundColor: Colors.white,
                foregroundColor: Colors.black87,
                icon: const Icon(Icons.call),
                label: const Text('Call'),
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
                    // Stays "SOS". This screen is reached far more often by
                    // picking somewhere to go than by pressing SOS, and on an
                    // ordinary walk this is a first, plain emergency button —
                    // the word everyone already knows beats naming a step
                    // that, on that path, was never outstanding.
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