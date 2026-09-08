import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'package:latlong2/latlong.dart';

import '../../services/api_client.dart';
import '../../services/directions_service.dart';

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

  const CaregiverNavigationScreen({
    super.key,
    required this.patientId,
    required this.patientName,
    this.initialLatitude,
    this.initialLongitude,
    this.alertMessage,
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
  LatLng? _routeFetchedFor;
  DateTime? _routeFetchedAt;
  bool _fittedOnce = false;

  @override
  void initState() {
    super.initState();
    if (widget.initialLatitude != null && widget.initialLongitude != null) {
      _patientLocation = LatLng(widget.initialLatitude!, widget.initialLongitude!);
    }
    _startCaregiverUpdates();
    _pollPatient();
    _patientPoll = Timer.periodic(_patientPollInterval, (_) => _pollPatient());
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
    setState(() {
      _caregiverLocation = LatLng(position.latitude, position.longitude);
    });
    _fitBothOnce();
    _refreshRouteIfWorthwhile();
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
                Expanded(
                  child: gmaps.GoogleMap(
                    initialCameraPosition: gmaps.CameraPosition(
                      target: gmaps.LatLng(them.latitude, them.longitude),
                      zoom: 15,
                    ),
                    onMapCreated: (c) {
                      _mapController = c;
                      _fitBothOnce();
                    },
                    myLocationEnabled: true,
                    markers: {
                      gmaps.Marker(
                        markerId: const gmaps.MarkerId('patient'),
                        position: gmaps.LatLng(them.latitude, them.longitude),
                        infoWindow: gmaps.InfoWindow(title: widget.patientName),
                        icon: gmaps.BitmapDescriptor.defaultMarkerWithHue(
                            gmaps.BitmapDescriptor.hueRed),
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
                      ],
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}
