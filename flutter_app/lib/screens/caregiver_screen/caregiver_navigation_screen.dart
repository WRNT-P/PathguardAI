import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'package:latlong2/latlong.dart';

import '../../services/api_client.dart';
import '../../services/directions_service.dart';
import '../../utils/bearing.dart';
import '../../utils/patient_marker.dart';

/// Routes the caregiver who claimed an SOS to the patient.
///
/// Deliberately to the patient and not to the safe place the patient is
/// walking to: the patient is the thing that can wander off, and a caregiver
/// sent to a fixed building has no way to notice they never arrived.
class CaregiverNavigationScreen extends StatefulWidget {
  final int patientId;
  final String patientName;

  /// Where the alert said the patient was. Drawn until the first live fix
  /// lands so the screen opens with a map instead of a spinner.
  final double? initialLatitude;
  final double? initialLongitude;

  /// The alert's own text, shown as context — it already names the safe
  /// place the patient is walking to. Passed through verbatim rather than
  /// parsed apart: the wording lives in the backend and a screen that picks
  /// it back up by string would break silently the day someone rephrases it.
  final String? alertMessage;

  /// The patient's photo, so they appear here as the same face the tracking
  /// map shows. Held on this device only, so frequently null.
  final File? profileImage;

  /// The alert this drive is answering, so it can be closed on arrival.
  ///
  /// Closing it has to live here. An "sos" alert never resolves itself — a
  /// person pressed the button, so a person decides when it is over — and the
  /// caregiver who claimed it is sent straight to this screen and no longer
  /// shown the alert on their home screen. Without this button the only way
  /// to end an emergency would be to edit the database.
  final int? alertId;

  const CaregiverNavigationScreen({
    super.key,
    required this.patientId,
    required this.patientName,
    this.initialLatitude,
    this.initialLongitude,
    this.alertMessage,
    this.profileImage,
    this.alertId,
  });

  @override
  State<CaregiverNavigationScreen> createState() => _CaregiverNavigationScreenState();
}

class _CaregiverNavigationScreenState extends State<CaregiverNavigationScreen> {
  static const _patientPollInterval = Duration(seconds: 15);

  /// How far the patient has to move before the route is worth asking Google
  /// for again, and how often that can happen at all. Directions is billed per
  /// call and this screen stays open for the length of a drive — without both
  /// limits a patient drifting a few metres every poll would bill a request
  /// every 15 seconds for as long as the search lasts.
  static const _routeRefetchDistanceM = 150.0;
  static const _routeRefetchInterval = Duration(seconds: 60);

  gmaps.GoogleMapController? _mapController;
  StreamSubscription<Position>? _positionSubscription;
  Timer? _patientPoll;

  LatLng? _caregiverLocation;
  LatLng? _patientLocation;
  DateTime? _patientFixAt;

  List<gmaps.LatLng>? _routePoints;
  List<RouteStep>? _routeSteps;
  int _currentStepIndex = 0;
  LatLng? _routeFetchedFor;
  DateTime? _routeFetchedAt;
  bool _fittedOnce = false;

  gmaps.BitmapDescriptor? _patientIcon;

  /// Which way the car is pointing, smoothed. Taken from movement between
  /// fixes rather than the magnetometer: a phone on a passenger seat faces
  /// whichever way it was put down, while the direction of travel at road
  /// speed is unambiguous.
  double? _travelBearing;

  bool _resolving = false;

  /// Whether the camera chases the caregiver. Turned off the moment they pan
  /// the map by hand — fighting a driver for control of their own map is
  /// worse than showing them the wrong part of it.
  bool _followCaregiver = true;

  @override
  void initState() {
    super.initState();
    if (widget.initialLatitude != null && widget.initialLongitude != null) {
      _patientLocation = LatLng(widget.initialLatitude!, widget.initialLongitude!);
    }
    _loadPatientIcon();
    _startCaregiverUpdates();
    _pollPatient();
    _patientPoll = Timer.periodic(_patientPollInterval, (_) => _pollPatient());
  }

  Future<void> _loadPatientIcon() async {
    final icon = await buildPatientMarkerIcon(widget.profileImage);
    if (mounted) setState(() => _patientIcon = icon);
  }

  @override
  void dispose() {
    _positionSubscription?.cancel();
    _patientPoll?.cancel();
    super.dispose();
  }

  Future<void> _startCaregiverUpdates() async {
    if (!await Geolocator.isLocationServiceEnabled()) return;
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      return;
    }

    // Cached fix first so the map is up immediately — same reason the patient
    // screens do it. The stream replaces it within seconds.
    try {
      final cached = await Geolocator.getLastKnownPosition();
      if (cached != null) _handleCaregiverPosition(cached);
    } catch (_) {}

    _positionSubscription = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 20,
      ),
    ).listen(_handleCaregiverPosition);
  }

  void _handleCaregiverPosition(Position position) {
    if (!mounted) return;
    final updated = LatLng(position.latitude, position.longitude);
    final previous = _caregiverLocation;
    const distance = Distance();

    setState(() {
      // A bearing computed across a few metres is mostly GPS noise; at road
      // speed the floor is passed on every fix anyway.
      if (previous != null &&
          distance.as(LengthUnit.Meter, previous, updated) >= 5) {
        final raw = calculateBearing(
          previous.latitude, previous.longitude, updated.latitude, updated.longitude);
        if (_travelBearing == null) {
          _travelBearing = raw;
        } else {
          final delta = shortestAngleDelta(_travelBearing!, raw);
          _travelBearing = (_travelBearing! + delta * 0.25) % 360;
          if (_travelBearing! < 0) _travelBearing = _travelBearing! + 360;
        }
      }

      _caregiverLocation = updated;

      // Walk the turn list forward as each step's end is reached.
      final steps = _routeSteps;
      if (steps != null && _currentStepIndex < steps.length - 1) {
        final end = steps[_currentStepIndex].endLocation;
        if (distance.as(LengthUnit.Meter, updated, LatLng(end.latitude, end.longitude)) <
            _stepAdvanceThresholdMeters) {
          _currentStepIndex++;
        }
      }
    });

    _fitBothOnce();
    _followCamera();
    _refreshRouteIfWorthwhile();
  }

  /// Distance at which a turn counts as taken. Wider than the patient
  /// screens' 15 m: a car passes a junction faster than a fix arrives.
  static const double _stepAdvanceThresholdMeters = 30;

  /// Keep the caregiver centred and the map turned the way they are driving,
  /// which is the whole difference between a map and a navigation screen.
  void _followCamera() {
    if (!_followCaregiver || !_fittedOnce) return;
    final me = _caregiverLocation;
    if (me == null) return;
    _mapController?.animateCamera(
      gmaps.CameraUpdate.newCameraPosition(
        gmaps.CameraPosition(
          target: gmaps.LatLng(me.latitude, me.longitude),
          zoom: 17.5,
          tilt: 45,
          bearing: _travelBearing ?? 0,
        ),
      ),
    );
  }

  Future<void> _pollPatient() async {
    try {
      final res = await apiGet(
        '/api/patients/${widget.patientId}/track',
        queryParams: {'hours': '6'},
      );
      if (res.statusCode != 200) return;
      final points = jsonDecode(res.body)['points'] as List;
      if (points.isEmpty) return;
      final last = points.last as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _patientLocation = LatLng(
          (last['latitude'] as num).toDouble(),
          (last['longitude'] as num).toDouble(),
        );
        final recordedAt = last['recorded_at'] as String?;
        _patientFixAt = recordedAt == null ? null : DateTime.parse(recordedAt).toLocal();
      });
      _fitBothOnce();
      _refreshRouteIfWorthwhile();
    } catch (_) {
      // A dropped poll is not worth surfacing — the next one is 15s away and
      // the last known position stays on screen meanwhile.
    }
  }

  /// Ask Google for a fresh route only when the patient has actually moved
  /// somewhere else, not every time either of us twitches.
  Future<void> _refreshRouteIfWorthwhile() async {
    final from = _caregiverLocation;
    final to = _patientLocation;
    if (from == null || to == null) return;

    const distance = Distance();
    final now = DateTime.now();
    if (_routeFetchedFor != null) {
      final moved = distance.as(LengthUnit.Meter, _routeFetchedFor!, to);
      final tooSoon = _routeFetchedAt != null &&
          now.difference(_routeFetchedAt!) < _routeRefetchInterval;
      if (moved < _routeRefetchDistanceM || tooSoon) return;
    }

    _routeFetchedFor = to;
    _routeFetchedAt = now;
    final route = await fetchRoute(
      gmaps.LatLng(from.latitude, from.longitude),
      gmaps.LatLng(to.latitude, to.longitude),
      mode: 'driving',
    );
    if (!mounted) return;
    setState(() {
      _routeSteps = route?.steps;
      _currentStepIndex = 0;
      // A straight line still says which way to set off, which beats an empty
      // map when Directions is unavailable.
      _routePoints = route?.points
              .map((p) => gmaps.LatLng(p.latitude, p.longitude))
              .toList() ??
          [
            gmaps.LatLng(from.latitude, from.longitude),
            gmaps.LatLng(to.latitude, to.longitude),
          ];
    });
  }

  /// Frame both of us once, then leave the camera alone — a map that keeps
  /// recentring is unusable for someone glancing at it between traffic lights.
  void _fitBothOnce() {
    if (_fittedOnce) return;
    final me = _caregiverLocation;
    final them = _patientLocation;
    final controller = _mapController;
    if (me == null || them == null || controller == null) return;
    _fittedOnce = true;
    controller.animateCamera(
      gmaps.CameraUpdate.newLatLngBounds(
        gmaps.LatLngBounds(
          southwest: gmaps.LatLng(
            me.latitude < them.latitude ? me.latitude : them.latitude,
            me.longitude < them.longitude ? me.longitude : them.longitude,
          ),
          northeast: gmaps.LatLng(
            me.latitude > them.latitude ? me.latitude : them.latitude,
            me.longitude > them.longitude ? me.longitude : them.longitude,
          ),
        ),
        80,
      ),
    );
  }

  /// The part of the route still ahead, drawn from where the caregiver is —
  /// same reasoning as the patient navigation screens: the route is fetched
  /// rarely, so the stored points would otherwise trail behind the car.
  List<gmaps.LatLng> _remainingRoute() {
    final route = _routePoints!;
    final me = _caregiverLocation;
    if (me == null) return route;

    const distance = Distance();
    var nearest = 0;
    var nearestDistance = double.infinity;
    for (var i = 0; i < route.length; i++) {
      final d = distance.as(
          LengthUnit.Meter, me, LatLng(route[i].latitude, route[i].longitude));
      if (d < nearestDistance) {
        nearestDistance = d;
        nearest = i;
      }
    }
    return [gmaps.LatLng(me.latitude, me.longitude), ...route.sublist(nearest)];
  }

  /// End the emergency. Behind a confirmation because it is the one action
  /// here that changes what every other caregiver sees, and a thumb on a
  /// phone propped in a car is not a considered decision.
  Future<void> _markResolved() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Reached ${widget.patientName}?'),
        content: const Text(
          'This closes the emergency for everyone. Only do it once the '
          'patient is safe with you.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Not yet'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Yes, they are safe'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _resolving = true);
    try {
      final res = await apiPatch('/api/alerts/${widget.alertId}',
          body: {'resolved': true});
      if (!mounted) return;
      if (res.statusCode == 200) {
        Navigator.of(context).pop();
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not close this alert (${res.statusCode})')),
      );
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Could not reach the server')),
      );
    } finally {
      if (mounted) setState(() => _resolving = false);
    }
  }

  RouteStep? get _currentStep {
    final steps = _routeSteps;
    if (steps == null || steps.isEmpty) return null;
    return steps[_currentStepIndex.clamp(0, steps.length - 1)];
  }

  /// Same mapping the patient's level 2 screen uses, so an instruction reads
  /// the same on both sides of the family.
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
      default:
        return Icons.straight;
    }
  }

  String get _distanceLabel {
    final me = _caregiverLocation;
    final them = _patientLocation;
    if (me == null || them == null) return 'Locating…';
    final metres = const Distance().as(LengthUnit.Meter, me, them);
    return metres < 1000
        ? '${metres.round()} m away'
        : '${(metres / 1000).toStringAsFixed(1)} km away';
  }

  @override
  Widget build(BuildContext context) {
    final them = _patientLocation;

    return Scaffold(
      appBar: AppBar(
        backgroundColor: Colors.red,
        foregroundColor: Colors.white,
        title: Text('Going to ${widget.patientName}'),
      ),
      body: them == null
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                if (_currentStep != null)
                  Container(
                    width: double.infinity,
                    color: Colors.red[700],
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
                    child: Row(
                      children: [
                        Icon(_instructionIcon(_currentStep!.instruction),
                            color: Colors.white, size: 34),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('${_currentStep!.distanceMeters.round()} m',
                                  style: TextStyle(color: Colors.red[100], fontSize: 13)),
                              Text(_currentStep!.instruction,
                                  style: const TextStyle(
                                      color: Colors.white,
                                      fontSize: 20,
                                      fontWeight: FontWeight.bold)),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                Expanded(
                  child: Stack(children: [
                    gmaps.GoogleMap(
                    initialCameraPosition: gmaps.CameraPosition(
                      target: gmaps.LatLng(them.latitude, them.longitude),
                      zoom: 15,
                    ),
                    onMapCreated: (c) {
                      _mapController = c;
                      _fitBothOnce();
                    },
                    // Panning by hand means they want to look at something.
                    // Snapping the camera back on the next fix would take it
                    // away again mid-glance.
                    onCameraMoveStarted: () {
                      if (_followCaregiver) setState(() => _followCaregiver = false);
                    },
                    myLocationEnabled: true,
                    markers: {
                      gmaps.Marker(
                        markerId: const gmaps.MarkerId('patient'),
                        position: gmaps.LatLng(them.latitude, them.longitude),
                        infoWindow: gmaps.InfoWindow(title: widget.patientName),
                        // Their face in a circle, the same as the tracking
                        // map. Falls back to a pin only until the bitmap is
                        // ready, which is a frame or two.
                        icon: _patientIcon ??
                            gmaps.BitmapDescriptor.defaultMarkerWithHue(
                                gmaps.BitmapDescriptor.hueRed),
                        anchor: const Offset(0.5, 0.5),
                      ),
                    },
                    polylines: {
                      if (_routePoints != null)
                        gmaps.Polyline(
                          polylineId: const gmaps.PolylineId('to_patient'),
                          points: _remainingRoute(),
                          color: Colors.red,
                          width: 5,
                        ),
                    },
                    ),
                    // Panning turns following off; this is the way back, and
                    // it only exists while it would do something.
                    if (!_followCaregiver)
                      Positioned(
                        right: 16,
                        bottom: 16,
                        child: FloatingActionButton.small(
                          onPressed: () {
                            setState(() => _followCaregiver = true);
                            _followCamera();
                          },
                          backgroundColor: Colors.white,
                          child: const Icon(Icons.navigation, color: Colors.red),
                        ),
                      ),
                  ]),
                ),
                SafeArea(
                  top: false,
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          _distanceLabel,
                          style: const TextStyle(
                              fontSize: 22, fontWeight: FontWeight.bold),
                        ),
                        if (widget.alertMessage != null) ...[
                          const SizedBox(height: 4),
                          Text(
                            widget.alertMessage!,
                            style: TextStyle(fontSize: 15, color: Colors.grey[700]),
                          ),
                        ],
                        if (_patientFixAt != null) ...[
                          const SizedBox(height: 4),
                          Text(
                            'Position updated '
                            '${_patientFixAt!.hour.toString().padLeft(2, '0')}:'
                            '${_patientFixAt!.minute.toString().padLeft(2, '0')}',
                            style: TextStyle(fontSize: 13, color: Colors.grey[600]),
                          ),
                        ],
                        if (widget.alertId != null) ...[
                          const SizedBox(height: 12),
                          SizedBox(
                            width: double.infinity,
                            child: ElevatedButton.icon(
                              onPressed: _resolving ? null : _markResolved,
                              icon: const Icon(Icons.check_circle_outline,
                                  color: Colors.white),
                              style: ElevatedButton.styleFrom(
                                backgroundColor: Colors.green[700],
                                minimumSize: const Size(0, 48),
                              ),
                              label: const Text("I've reached them",
                                  style: TextStyle(color: Colors.white, fontSize: 16)),
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}
