import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'sos_contact_screen.dart';
import '../../services/sos_service.dart';
import '../../services/safe_zone_service.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';
import '../../services/active_trip_service.dart';
import '../../utils/bearing.dart';
import '../../services/directions_service.dart';
import '../../services/trip_event_reporter.dart';
import '../../utils/route_deviation.dart';
import '../../theme/patient_theme.dart';
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

  /// This screen's claim on `active_trips/{patientId}` — see [dispose].
  ActiveTripHandle? _tripHandle;

  /// Guards against reporting "arrived" more than once per trip —
  /// [_handlePosition] fires on every GPS update, and the distance check
  /// alone would re-fire for as long as the patient stands near the place.
  bool _arrivalReported = false;

  /// Off-route is reported once per continuous episode: set the moment the
  /// patient first strays past [_offRouteThresholdMeters], cleared the
  /// moment they're back within it, so straying twice on one walk is two
  /// notifications, not zero after the first.
  DateTime? _offRouteSince;
  bool _offRouteReported = false;
  static const double _offRouteThresholdMeters = 80;
  static const Duration _offRouteSustainedFor = Duration(seconds: 30);

  /// The patient's own last-known risk score, polled read-only from
  /// `/risk/latest` (never `/risk` — that recomputes and can push).
  double? _riskScore;
  Timer? _riskPoll;
  static const double _riskRescueThreshold = 80;

  /// Name of whoever claimed this patient's open "emergency" alert, if any —
  /// read from the same `alerts` row a caregiver's `SosAlertScreen` claims
  /// through. Only ever set when a real person has actually claimed it, on
  /// purpose: telling a patient "help is coming" before anyone has agreed to
  /// go would be a promise the app cannot back up.
  String? _claimedByName;

  /// North-up, or turned the way they are walking. Same control the level 2
  /// screen carries: a map that rotates is easier to walk by, and a map that
  /// stays north-up is easier to read against street signs — which one helps
  /// is the patient's answer, not ours.
  bool _northUp = true;

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

    // The press leaves for the server first, before anything else is known.
    // It used to wait up to three seconds for Google to name a safe place so
    // the alert could carry a destination, which put Google's latency between
    // a patient in trouble and their family. The destination is not lost by
    // going first: the walk publishes it to `active_trips` the moment it
    // starts, and the caregiver's SOS screen reads it from there.
    final sosSent = triggerSOS().catchError((_) => false);

    final safePlace = await placeLookup.catchError((_) => null);
    await sosSent;
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
        title: const Text('ส่งการแจ้งเตือนแล้ว', style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
        content: Text(
          nearestName != null
              ? 'แจ้ง $nearestName ผู้ดูแลที่อยู่ใกล้คุณที่สุดแล้ว'
              : 'แจ้งผู้ดูแลของคุณแล้ว',
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
            child: const Text('ตกลง', style: TextStyle(fontSize: 18)),
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
    _publishActiveTrip();
    _pollRisk();
    _riskPoll = Timer.periodic(const Duration(seconds: 30), (_) => _pollRisk());
  }

  /// Read-only, safe to poll — unlike `GET /api/risk/{id}` this never
  /// recomputes or pushes. A missed poll just means the button doesn't light
  /// up a little late; never worth surfacing as an error to the patient.
  Future<void> _pollRisk() async {
    final patientId = Session.instance.patientId;
    if (patientId == null) return;
    try {
      final res = await apiGet('/api/patients/$patientId/risk/latest');
      if (res.statusCode == 200) {
        final body = jsonDecode(res.body) as Map<String, dynamic>;
        final score = (body['risk_score'] as num?)?.toDouble();
        if (mounted) setState(() => _riskScore = score);
      }
    } catch (_) {}

    // Same poll cycle, not its own timer: this only matters while risk is
    // already elevated, and a stale claimed-by name for a few extra seconds
    // costs nothing a patient would notice.
    try {
      final alertsRes = await apiGet('/api/patients/$patientId/alerts?limit=100');
      if (alertsRes.statusCode == 200) {
        final alerts = (jsonDecode(alertsRes.body)['alerts'] as List)
            .cast<Map<String, dynamic>>();
        final emergency = alerts.cast<Map<String, dynamic>?>().firstWhere(
              (a) => a?['alert_type'] == 'emergency' && a?['resolved'] == false,
              orElse: () => null,
            );
        if (mounted) {
          setState(() => _claimedByName = emergency?['claimed_by_name'] as String?);
        }
      }
    } catch (_) {}
  }

  /// Tell the caregiver's screen a trip is under way, for as long as this
  /// screen is open. Fire-and-forget by design: the service swallows its own
  /// failures, because a Realtime Database outage must not stand between a
  /// patient and the directions home.
  void _publishActiveTrip() {
    final patientId = Session.instance.patientId;
    if (patientId == null) return;
    _tripHandle =
        ActiveTripService.instance.start(patientId: patientId, place: widget.place);
    reportTripEvent(
      'started',
      destinationName: widget.place['name'] as String?,
      latitude: (widget.place['lat'] as num?)?.toDouble(),
      longitude: (widget.place['lng'] as num?)?.toDouble(),
    );
  }

  /// Checks the just-updated position against [_destination] (arrival) and
  /// the fetched route (off-route), reporting each at most once per episode.
  void _checkTripProgress(LatLng updated) {
    if (!_arrivalReported) {
      final toDestination = const Distance().as(
        LengthUnit.Meter, updated, LatLng(_destination.latitude, _destination.longitude));
      if (toDestination < 20) {
        _arrivalReported = true;
        reportTripEvent(
          'arrived',
          destinationName: widget.place['name'] as String?,
          latitude: updated.latitude,
          longitude: updated.longitude,
        );
      }
    }

    final route = _routePoints;
    if (route == null || route.length < 2) return;
    final routeLatLng = route.map((p) => LatLng(p.latitude, p.longitude)).toList();
    final offRoute = distanceToRoute(updated, routeLatLng) > _offRouteThresholdMeters;
    if (!offRoute) {
      _offRouteSince = null;
      _offRouteReported = false;
      return;
    }
    _offRouteSince ??= DateTime.now();
    if (!_offRouteReported &&
        DateTime.now().difference(_offRouteSince!) >= _offRouteSustainedFor) {
      _offRouteReported = true;
      reportTripEvent('off_route', latitude: updated.latitude, longitude: updated.longitude);
    }
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
          color: Colors.blue,
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

      if (_routeSteps != null && _currentStepIndex < _routeSteps!.length - 1) {
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
      final route = _routePoints;
      if (route != null && route.isNotEmpty) {
        final claimed = route[_routeProgressIndex.clamp(0, route.length - 1)];
        final distanceFromClaimed = distance.as(
            LengthUnit.Meter, updated, LatLng(claimed.latitude, claimed.longitude));

        if (distanceFromClaimed > _offRouteThresholdMeters) {
          // The point this index claims to be "here" is nowhere near the
          // patient right now. A GPS jump — emulator location teleporting for
          // testing, or a real signal drop that resumes somewhere else — used
          // to walk this index forward against wherever that stray fix
          // landed, and since the loop below only ever advances, the index
          // stayed stuck far ahead once the real position came back: the
          // drawn line jumped from here straight out to that stale point
          // before continuing normally. Re-anchor to whichever point is
          // actually nearest, but only if that point is itself close enough
          // to trust — otherwise leave the index alone rather than snapping
          // the line onto an unrelated leg of the route.
          var nearestIndex = _routeProgressIndex;
          var nearestMeters = distanceFromClaimed;
          for (var i = 0; i < route.length; i++) {
            final d = distance.as(
                LengthUnit.Meter, updated, LatLng(route[i].latitude, route[i].longitude));
            if (d < nearestMeters) {
              nearestMeters = d;
              nearestIndex = i;
            }
          }
          if (nearestMeters <= _offRouteThresholdMeters) {
            _routeProgressIndex = nearestIndex;
          }
        } else {
          // Forward-only, and only while the next point is genuinely nearer
          // than the current one, so a route that doubles back near itself
          // cannot snap the line onto the wrong leg.
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
      }

      _currentLocation = updated;
    });

    _checkTripProgress(updated);
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
      case 'เลี้ยวซ้าย':
        return Icons.turn_left;
      case 'เลี้ยวขวา':
        return Icons.turn_right;
      case 'กลับหลังหัน':
        return Icons.u_turn_left;
      case 'ผ่านวงเวียน':
        return Icons.roundabout_left;
      case 'ใกล้ถึงจุดหมายแล้ว':
        return Icons.flag;
      case 'เดินย้อนกลับทางเดิม':
        return Icons.u_turn_left;
      case 'กลับถึงจุดเริ่มต้นแล้ว':
        return Icons.flag;
      default:
        return Icons.straight;
    }
  }

  /// How far ahead of the patient (in metres) the tilted camera looks —
  /// this is what actually pushes their marker down toward the bottom of
  /// the screen. Maps' own `padding` property was tried for this first (it
  /// only repositions on-screen controls and affects bounds-fitting camera
  /// moves, not where a plain lat/lng target renders — a real screenshot
  /// showed the marker still dead-centre at padding fractions up to 0.48)
  /// and replaced with this: centre the camera on a point projected ahead
  /// along the direction of travel instead of on the patient's own
  /// position, so the patient's real position renders behind that point —
  /// i.e. toward the bottom of the screen — the same way any chase camera
  /// looks ahead of what it's following.
  static const double _tiltedLookaheadMeters = 80;

  /// Point the camera at the patient with whichever bearing [_northUp] calls
  /// for. Its own method so the toggle can apply immediately instead of
  /// waiting for the next GPS fix to move the camera.
  void _updateCamera({double zoom = 18.5}) {
    final current = _currentLocation;
    if (current == null) return;
    final bearing = _travelBearing ?? 0;
    final cameraTarget = _northUp
        ? current
        : const Distance().offset(current, _tiltedLookaheadMeters, bearing);
    // newLatLngZoom cannot carry a bearing — newCameraPosition is the one
    // that keeps the rotation instead of snapping back to north-up.
    _mapController?.animateCamera(
      gmaps.CameraUpdate.newCameraPosition(
        gmaps.CameraPosition(
          target: gmaps.LatLng(cameraTarget.latitude, cameraTarget.longitude),
          zoom: zoom,
          bearing: _northUp ? 0 : bearing,
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
            trailing: Text('${step.distanceMeters.toStringAsFixed(0)} ม.'),
          );
        },
      ),
    );
  }

  @override
  void dispose() {
    _positionSubscription?.cancel();
    _riskPoll?.cancel();
    // Not awaited — dispose cannot be async — and it does not need to be:
    // if the write never lands, the heartbeat has already stopped and the
    // trip ages out on the reader's side within staleAfter. Ending through
    // the handle means the SOS redirect's replacement screen, which starts
    // its own trip before this one is disposed, is not wiped by it.
    _tripHandle?.end();
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
          zIndexInt: 1,
        )
    };
    final polylines = <gmaps.Polyline>{
      if (_routePoints != null)
        gmaps.Polyline(
          polylineId: const gmaps.PolylineId('route'),
          points: _remainingRoute(),
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
          : 'ใกล้ถึงจุดหมายแล้ว';
    }

    // Only once risk is genuinely high AND a real caregiver has claimed the
    // alert — see [_claimedByName]'s doc for why the second half is required.
    final beingRescued =
        (_riskScore ?? 0) > _riskRescueThreshold && _claimedByName != null;

    return Scaffold(
      appBar: AppBar(
        title: Text(widget.place['name']),
        actions: [
          IconButton(
            icon: const Icon(Icons.list),
            tooltip: 'ดูเส้นทางทั้งหมด',
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
            markers: markers,
            polylines: polylines,
            // Both of Maps' own bottom-right controls are replaced by our
            // custom recenter FAB below — leaving either enabled put a
            // second, unlabeled tap target in the exact same corner as ours.
            zoomControlsEnabled: false,
            myLocationButtonEnabled: false,
            onMapCreated: (controller) {
              _mapController = controller;
            },
          ),
          // Stacked in one Column, highest-priority first, instead of three
          // independent `top: 0` Positioneds — those overlapped whenever more
          // than one condition held at once (a real case now: high risk plus
          // mid-turn instructions), each banner painting over the last.
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: SafeArea(
              child: Column(
                children: [
                  if (beingRescued)
                    Container(
                      margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                      decoration: BoxDecoration(
                        color: Colors.green.shade700,
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.favorite, color: Colors.white, size: 40),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Text(
                              'รออยู่ตรงนี้นะ $_claimedByName กำลังมารับ',
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 20,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  if (_locationUnavailable)
                    Container(
                      margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
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
                              'เปิดตำแหน่ง (GPS) เพื่อเริ่มนำทาง',
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
                  if (instructionText != null)
                    Container(
                      margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
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
                                    '${distanceToTurn.toStringAsFixed(0)} ม.',
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
                ],
              ),
            ),
          ),
          // Whole bottom control cluster lives in one Positioned so its two
          // bands — the right-hand "take me back"/recenter stack, and the
          // call/SOS row below it — are laid out relative to each other
          // instead of as independent `bottom:` Positioneds that used to land
          // on the same strip of screen and overlap. SafeArea keeps all of it
          // clear of a gesture-nav bar on the physical devices this was
          // screenshotted on.
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: SafeArea(
              top: false,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    // Bottom row: Call (left) and SOS (center) laid out in a
                    // Stack sized to SOS's own footprint, so Call is anchored
                    // to the far left edge and can never drift into SOS's
                    // circle regardless of screen width.
                    SizedBox(
                      // Tall enough for the left column (recenter + compass +
                      // Call, 176 total) with SOS still anchored to the same
                      // bottom edge — Stack clips to its own box by default,
                      // so this has to fit the tallest child or the top
                      // button gets silently cut off.
                      height: 176,
                      child: Stack(
                        children: [
                          // Bottom left: recenter, north-up, then Call,
                          // stacked so none of them ever share a tap zone
                          // with each other, with SOS, or with Maps' own
                          // (now-disabled) zoom controls. Recenter and
                          // north-up match each other's size/shape/elevation
                          // on purpose — a matched pair of map controls,
                          // both acting on the map itself, distinct from
                          // Call below.
                          Align(
                            alignment: Alignment.bottomLeft,
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Semantics(
                                  button: true,
                                  label: 'เลื่อนแผนที่มาที่ตำแหน่งของฉัน',
                                  child: SizedBox(
                                    width: 48,
                                    height: 48,
                                    child: FloatingActionButton(
                                      heroTag: 'recenter',
                                      tooltip: 'กลับมาที่ตำแหน่งของฉัน',
                                      backgroundColor: Colors.white,
                                      elevation: 3,
                                      onPressed: _recenterOnPatient,
                                      child: const Icon(Icons.my_location,
                                          color: PatientColors.berry),
                                    ),
                                  ),
                                ),
                                const SizedBox(height: 12),
                                Semantics(
                                  button: true,
                                  label: _northUp
                                      ? 'สลับเป็นแผนที่หันตามทิศที่เดิน'
                                      : 'สลับเป็นแผนที่ทิศเหนืออยู่ด้านบน',
                                  child: SizedBox(
                                    width: 48,
                                    height: 48,
                                    child: FloatingActionButton(
                                      heroTag: 'northUpToggle',
                                      tooltip: _northUp
                                          ? 'หันตามทิศที่เดิน'
                                          : 'ทิศเหนืออยู่ด้านบน',
                                      backgroundColor:
                                          _northUp ? Colors.white : PatientColors.berry,
                                      elevation: 3,
                                      onPressed: _toggleNorthUp,
                                      child: Icon(Icons.explore,
                                          color: _northUp ? PatientColors.berry : Colors.white),
                                    ),
                                  ),
                                ),
                                const SizedBox(height: 12),
                                // Ringing a person the patient knows is a
                                // different kind of help from the red button —
                                // quieter, and sometimes all they actually want.
                                FloatingActionButton.extended(
                                  heroTag: 'contacts',
                                  onPressed: () => Navigator.push(
                                    context,
                                    MaterialPageRoute(
                                        builder: (context) => const SosContactsScreen()),
                                  ),
                                  backgroundColor: Colors.white,
                                  foregroundColor: PatientColors.charcoal,
                                  elevation: 3,
                                  icon: const Icon(Icons.call),
                                  label: const Text(
                                    'โทร',
                                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
                                  ),
                                ),
                              ],
                            ),
                          ),
                          Align(
                            alignment: Alignment.bottomCenter,
                            child: Semantics(
                              button: true,
                              label:
                                  'ปุ่มฉุกเฉิน SOS กดเพื่อแจ้งผู้ดูแลและนำทางไปที่ปลอดภัย',
                              child: SizedBox(
                                width: 96,
                                height: 96,
                                child: FloatingActionButton(
                                  heroTag: 'sos',
                                  onPressed: _sosSending ? null : _handleSOS,
                                  backgroundColor: Colors.red,
                                  elevation: 4,
                                  shape: const CircleBorder(),
                                  // Stays "SOS". This screen is reached far
                                  // more often by picking somewhere to go
                                  // than by pressing SOS, and on an ordinary
                                  // walk this is a first, plain emergency
                                  // button — the word everyone already knows
                                  // beats naming a step that, on that path,
                                  // was never outstanding.
                                  child: const Text(
                                    'SOS',
                                    style: TextStyle(
                                        color: Colors.white,
                                        fontWeight: FontWeight.bold,
                                        fontSize: 20),
                                  ),
                                ),
                              ),
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
        ],
      ),
    );
  }
}