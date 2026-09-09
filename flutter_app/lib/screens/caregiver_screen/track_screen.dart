import 'dart:convert';
import '../../services/api_client.dart';
import '../../services/active_trip_service.dart';
import '../../utils/patient_marker.dart';

import 'package:flutter/material.dart';
import 'dart:io';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'package:latlong2/latlong.dart';
import 'dart:async';
import '../../services/location_service.dart';

class TrackScreen extends StatefulWidget{
  final Map<String, dynamic> patient;
  const TrackScreen({super.key, required this.patient});

  @override
  State<TrackScreen> createState() => _TrackScreenState();
}

class _TrackScreenState extends State<TrackScreen>{
  Timer? _timer;
  LatLng? _currentLocation;
  DateTime? _lastUpdated;

  /// Whether the patient's own recent track shows them actually moving.
  ///
  /// This used to be `activeAlert != null`, which answered a completely
  /// different question: a patient sitting still at home with any unresolved
  /// alert — an SOS nobody closed, a wandering alert that outlived the
  /// condition — read as "Traveling", while one genuinely walking away with no
  /// alert yet read as stationary. Movement is a property of the track, so it
  /// is now measured from the track.
  bool _isMoving = false;

  /// The trip the patient's own device says it is navigating, or null.
  ///
  /// A stronger signal than [_isMoving] where it exists — it names the
  /// destination and appears the instant they set off, instead of waiting for
  /// enough track to prove displacement. It is not a replacement for it: a
  /// patient who wanders out without opening the app never writes this, and
  /// that is precisely the patient this product is for.
  ActiveTrip? _activeTrip;
  StreamSubscription<ActiveTrip?>? _activeTripSubscription;
  double? _riskScore;
  String? _riskLevel;
  DateTime? _riskCalculatedAt;
  List<Map<String, dynamic>> _places = [];
  gmaps.GoogleMapController? _mapController;
  gmaps.BitmapDescriptor? _patientIcon;

  /// places.py's DEFAULT_RADIUS_M, for pins written before radii existed.
  static const double _defaultPlaceRadiusM = 150.0;

  /// The pinned place the patient is currently standing inside, or null.
  ///
  /// Mirrors the backend's ``find_nearest_cluster``: a point counts as
  /// somewhere they know when it falls inside SOME pin's OWN radius, not
  /// within one fixed distance of home. The two definitions have to agree —
  /// this screen and the risk formula describing the same point differently is
  /// exactly the split-brain the stop/confusion classifier had until
  /// ``familiarity_at`` (2026-09-08), where a patient 279 m from a 400 m home
  /// pin was simultaneously at home and nowhere familiar.
  Map<String, dynamic>? _placeContaining(LatLng point) {
    for (final place in _places) {
      final lat = (place['latitude'] as num?)?.toDouble();
      final lng = (place['longitude'] as num?)?.toDouble();
      if (lat == null || lng == null) continue;
      final radius = (place['radius_m'] as num?)?.toDouble() ?? _defaultPlaceRadiusM;
      final metres = const Distance().as(LengthUnit.Meter, LatLng(lat, lng), point);
      if (metres <= radius) return place;
    }
    return null;
  }

  /// How far back to look when deciding "are they moving right now".
  static const Duration _movementWindow = Duration(minutes: 5);

  /// Displacement inside that window that counts as travelling rather than
  /// GPS noise. A consumer phone fix drifts tens of metres while sitting on a
  /// table, so anything below this would call a sleeping patient a walking one.
  static const double _movingDisplacementM = 60.0;

  /// Past this, the last fix is too old to describe the patient at all — the
  /// phone is off, out of signal, or the uploader has stopped. Saying either
  /// "Traveling" or "At safe place" from an hour-old point is a guess dressed
  /// as an observation, so the panel says the fix is old instead.
  static const Duration _staleFixAfter = Duration(minutes: 15);

  /// The whole recent track, newest last — `points.last` is the current
  /// position, and the tail before it is what movement is measured against.
  Future<List<Map<String, dynamic>>> _fetchRecentTrack() async {
    final res = await apiGet('/api/patients/${widget.patient['id']}/track', queryParams: {'hours': '6'});
    if (res.statusCode != 200) return [];
    final data = jsonDecode(res.body);
    return (data['points'] as List).cast<Map<String, dynamic>>();
  }

  /// True when the track moved more than [_movingDisplacementM] away from the
  /// latest fix at some point in the last [_movementWindow].
  ///
  /// Measured against the newest point's own timestamp, not wall-clock now: a
  /// track that stopped an hour ago must not be re-read as "moving" simply
  /// because its final two points happened to be far apart. Staleness is a
  /// separate question, answered by [_lastFixIsStale].
  bool _trackShowsMovement(List<Map<String, dynamic>> points) {
    if (points.length < 2) return false;
    final latest = points.last;
    final latestAt = DateTime.tryParse(latest['recorded_at'] as String? ?? '');
    if (latestAt == null) return false;
    final latestPoint = LatLng(
      (latest['latitude'] as num).toDouble(),
      (latest['longitude'] as num).toDouble(),
    );

    for (final point in points.reversed.skip(1)) {
      final recordedAt = DateTime.tryParse(point['recorded_at'] as String? ?? '');
      if (recordedAt == null) continue;
      if (latestAt.difference(recordedAt) > _movementWindow) break;
      final metres = const Distance().as(
        LengthUnit.Meter,
        latestPoint,
        LatLng((point['latitude'] as num).toDouble(), (point['longitude'] as num).toDouble()),
      );
      if (metres > _movingDisplacementM) return true;
    }
    return false;
  }

  bool get _lastFixIsStale =>
      _lastUpdated == null || DateTime.now().difference(_lastUpdated!) > _staleFixAfter;

  /// GET .../risk/latest — read-only, safe to poll. Never GET /api/risk/{id}
  /// here: that one recomputes and can write an alert + push on every call.
  Future<Map<String, dynamic>?> _fetchLatestRisk() async {
    final res = await apiGet('/api/patients/${widget.patient['id']}/risk/latest');
    if (res.statusCode != 200) return null;
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  /// Pins don't change often — fetched once here instead of on the 15s
  /// poll, and independently of whatever `widget.patient['home']` was passed
  /// in (that one only ever carried the home pin, never the other safe
  /// places added in the same form).
  Future<void> _fetchPlaces() async {
    final res = await apiGet('/api/patients/${widget.patient['id']}/places');
    if (res.statusCode != 200) return;
    final places = (jsonDecode(res.body)['places'] as List).cast<Map<String, dynamic>>();
    if (!mounted) return;
    setState(() => _places = places);
    _fitCameraToPatientAndNearestPlace();
  }

  /// Renders the patient's marker as a circular avatar (their profile photo,
  /// cropped to a circle with a white ring) instead of the default map pin —
  /// no image content is possible on a plain Circle overlay, so this has to
  /// be a custom Marker bitmap with a center anchor instead of a pin anchor.
  Future<void> _loadPatientIcon() async {
    final icon = await buildPatientMarkerIcon(widget.patient['profileImage'] as File?);
    if (mounted) setState(() => _patientIcon = icon);
  }


  @override
  void initState() {
    super.initState();
    _loadPatientIcon();
    _fetchPlaces();
    _watchActiveTrip();
    _timer = Timer.periodic(const Duration(seconds: 15), (timer) async {
      final points = await _fetchRecentTrack();
      final risk = await _fetchLatestRisk();
      if (!mounted) return;

      setState(() {
        if (points.isNotEmpty) {
          final point = points.last;
          _currentLocation = LatLng(
            (point['latitude'] as num).toDouble(),
            (point['longitude'] as num).toDouble(),
          );
          final recordedAt = point['recorded_at'] as String?;
          if (recordedAt != null) {
            _lastUpdated = DateTime.parse(recordedAt).toLocal();
          }
          _isMoving = _trackShowsMovement(points);
        }

        if (risk != null && risk['status'] == 'ok') {
          _riskScore = (risk['risk_score'] as num?)?.toDouble();
          _riskLevel = risk['risk_level'] as String?;
          final calculatedAt = risk['calculated_at'] as String?;
          _riskCalculatedAt = calculatedAt != null ? DateTime.parse(calculatedAt).toLocal() : null;
        } else {
          _riskScore = null;
          _riskLevel = null;
          _riskCalculatedAt = null;
        }
      });
      _fitCameraToPatientAndNearestPlace();
    });
  }

  /// Realtime, not polled: the point of this signal over the track-based one
  /// is that it lands the moment the patient sets off, and a 15 s poll would
  /// throw most of that away.
  void _watchActiveTrip() {
    final patientId = (widget.patient['id'] as num?)?.toInt();
    if (patientId == null) return;
    _activeTripSubscription =
        ActiveTripService.instance.watch(patientId).listen((trip) {
      if (!mounted) return;
      setState(() => _activeTrip = trip);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _activeTripSubscription?.cancel();
    super.dispose();
  }

  Map<String, dynamic>? get _nearestPlace {
    if (_currentLocation == null || _places.isEmpty) return null;
    Map<String, dynamic>? nearest;
    double? nearestDistance;
    for (final place in _places) {
      final placeLatLng = LatLng(
        (place['latitude'] as num).toDouble(),
        (place['longitude'] as num).toDouble(),
      );
      final distance = const Distance().as(LengthUnit.Meter, _currentLocation!, placeLatLng);
      if (nearestDistance == null || distance < nearestDistance) {
        nearest = place;
        nearestDistance = distance;
      }
    }
    return nearest;
  }

  /// Zoom used whenever the camera focuses on the patient alone. Google Maps
  /// only draws building footprints from roughly zoom 17, and street-level
  /// detail is the whole point of looking at a patient who is somewhere
  /// unexpected — below this the screen shows a dot on an empty road grid.
  static const double _patientFocusZoom = 17.0;

  /// Past this far apart, the safe place stops being useful context and the
  /// fit below is dropped in favour of the patient alone.
  ///
  /// ⚠️ A judgement call, not a measurement. It is here because the fit had no
  /// ceiling at all: the camera pulled back far enough to hold both points
  /// however far apart they were, so **the further a patient wandered the less
  /// the caregiver could see** — the map was at its least useful in exactly the
  /// situation it exists for. Live data made that concrete: a patient 25 km
  /// from their home pin put the camera near zoom 10, where the patient is a
  /// dot and no street is readable.
  static const double _maxFitDistanceM = 2000;

  /// Zooms/pans so both the patient and their nearest safe place are on
  /// screen together — a caregiver checking this screen wants "how far from
  /// safety are they", not just a dot with no reference point.
  ///
  /// Unless they are too far apart to hold both usefully, in which case
  /// "where are they, exactly" wins over "how far from home" — the distance is
  /// already stated in words in the panel below the map, and a caregiver
  /// reading it does not need the second marker on screen to learn it.
  void _fitCameraToPatientAndNearestPlace() {
    final controller = _mapController;
    final current = _currentLocation;
    final nearest = _nearestPlace;
    if (controller == null || current == null) return;

    void focusOnPatient() {
      controller.animateCamera(
        gmaps.CameraUpdate.newLatLngZoom(
          gmaps.LatLng(current.latitude, current.longitude),
          _patientFocusZoom,
        ),
      );
    }

    if (nearest == null) {
      focusOnPatient();
      return;
    }

    final nearestLat = (nearest['latitude'] as num).toDouble();
    final nearestLng = (nearest['longitude'] as num).toDouble();

    // Same Distance() the nearest-place search above uses, so the two cannot
    // disagree about how far apart these points are.
    final metresApart = const Distance().as(
      LengthUnit.Meter, current, LatLng(nearestLat, nearestLng));
    if (metresApart > _maxFitDistanceM) {
      focusOnPatient();
      return;
    }

    final bounds = gmaps.LatLngBounds(
      southwest: gmaps.LatLng(
        current.latitude < nearestLat ? current.latitude : nearestLat,
        current.longitude < nearestLng ? current.longitude : nearestLng,
      ),
      northeast: gmaps.LatLng(
        current.latitude > nearestLat ? current.latitude : nearestLat,
        current.longitude > nearestLng ? current.longitude : nearestLng,
      ),
    );
    controller.animateCamera(gmaps.CameraUpdate.newLatLngBounds(bounds, 80));
  }

  String _riskAgeLabel() {
    if (_riskCalculatedAt == null) return '';
    final ageMinutes = DateTime.now().difference(_riskCalculatedAt!).inMinutes;
    if (ageMinutes < 1) return ' just now';
    if (ageMinutes < 60) return ' $ageMinutes min ago';
    final ageHours = ageMinutes ~/ 60;
    return ' ${ageHours}h ago';
  }

  String _riskLevelLabel(String? level) {
    switch (level) {
      case 'high':
        return 'High';
      case 'medium':
        return 'Medium';
      case 'low':
        return 'Low';
      default:
        return 'Unknown';
    }
  }

  String _formatTime(DateTime time) {
    return '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';
  }

  /// A clock time alone hides how old it is — a caregiver glancing at "11:20"
  /// has to know the current time to notice the fix is 90 minutes cold. Once
  /// the fix is stale the age is spelled out beside it.
  String _lastUpdatedLabel() {
    if (_lastUpdated == null) return 'Waiting for location...';
    final at = _formatTime(_lastUpdated!);
    if (!_lastFixIsStale) return 'Location last updated $at';
    final minutes = DateTime.now().difference(_lastUpdated!).inMinutes;
    final age = minutes < 60 ? '$minutes min ago' : '${minutes ~/ 60}h ${minutes % 60}m ago';
    return 'Location last updated $at — $age';
  }

  @override
  Widget build(BuildContext context) {
    final profileImage = widget.patient['profileImage'] as File?;
    final patientName = widget.patient['name'] as String? ?? 'Patient';
    // "At safe place" used to mean nothing more than "no unresolved alert", so
    // it stayed green with the patient kilometres from anywhere they know — an
    // alert only exists once risk has recomputed (60 s throttle) and survived
    // its push cooldown, and it is cleared the moment the condition passes.
    // The label now answers the question it appears to answer.
    final atKnownPlace =
        _currentLocation != null && _placeContaining(_currentLocation!) != null;
    // An old fix says nothing about where the patient is now, so it outranks
    // every question below it.
    final fixIsStale = _lastFixIsStale;
    // Re-checked here rather than trusted from the last stream event: the
    // stream only fires on change, so a trip whose heartbeat simply stopped
    // would otherwise stay on screen as live until the node changed again.
    final trip = _activeTrip?.isFresh == true ? _activeTrip : null;
    final onTrip = !fixIsStale && trip != null;
    final isTraveling = !fixIsStale && !onTrip && _isMoving;
    final statusColor = fixIsStale
        ? Colors.grey[600]!
        : onTrip
            ? Colors.blue[700]!
            : isTraveling || !atKnownPlace
                ? Colors.orange[800]!
                : Colors.green[700]!;
    final statusIcon = fixIsStale
        ? Icons.location_disabled_rounded
        : onTrip
            ? Icons.navigation_rounded
            : isTraveling
                ? Icons.directions_walk_rounded
                : atKnownPlace
                    ? Icons.home_rounded
                    : Icons.explore_off_rounded;
    // A named destination beats "Traveling": it is the difference between a
    // caregiver knowing to leave them to it and having to go and look.
    final statusLabel = fixIsStale
        ? 'No recent signal'
        : onTrip
            ? (trip.destinationName?.isNotEmpty == true
                ? 'On a trip to ${trip.destinationName}'
                : 'On a trip')
            : isTraveling
                ? 'Traveling'
                : atKnownPlace
                    ? 'At safe place'
                    : 'Away from safe places';
    final homePlace = widget.patient['home'] as ParsedLocation?;
    double? distanceInMeters;

    final riskColor = _riskLevel == 'high'
        ? Colors.red[700]!
        : _riskLevel == 'medium'
            ? Colors.orange[800]!
            : Colors.green[700]!;
    final riskBg = _riskLevel == 'high'
        ? Colors.red[50]!
        : _riskLevel == 'medium'
            ? Colors.orange[50]!
            : Colors.green[50]!;

    if (homePlace != null && _currentLocation != null) {
      final homeLatLng = LatLng(homePlace.latitude, homePlace.longitude);
      distanceInMeters = const Distance().as(LengthUnit.Meter, homeLatLng, _currentLocation!);
    }

    final isHighRisk = _riskLevel == 'high';

    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: Colors.grey[300],
        elevation: 0,
        titleSpacing: 0,
        title: Row(
          children: [
            CircleAvatar(
              radius: 20,
              backgroundColor: Colors.grey[400],
              backgroundImage: profileImage != null ? FileImage(profileImage) : null,
              child: profileImage == null
                  ? Icon(Icons.person, size: 26, color: Colors.grey[800])
                  : null,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                patientName,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.bold,
                  color: Colors.black87,
                ),
              ),
            ),
          ],
        ),
      ),
      body: Column(
        children: [
          // Highest-visibility element on the screen when risk is high — a
          // caregiver who opens Track directly (not via the SOS interrupt
          // flow) still needs the risk state to be unmissable at a glance.
          if (isHighRisk)
            Semantics(
              liveRegion: true,
              label: 'Warning: high wandering risk for $patientName',
              child: Container(
                width: double.infinity,
                color: Colors.red[700],
                padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 16),
                child: Row(
                  children: [
                    const Icon(Icons.warning_amber_rounded, color: Colors.white, size: 26),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        'High risk right now — check on $patientName',
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          Expanded(
            child: Stack(
              children: [
                gmaps.GoogleMap(
                  onMapCreated: (controller) {
                    _mapController = controller;
                    _fitCameraToPatientAndNearestPlace();
                  },
                  initialCameraPosition: gmaps.CameraPosition(
                    target: _currentLocation != null
                        ? gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude)
                        : const gmaps.LatLng(13.7563, 100.5018),
                    zoom: 16.0,
                  ),
                  markers: {
                    if (_currentLocation != null)
                      gmaps.Marker(
                        markerId: const gmaps.MarkerId('patient'),
                        position: gmaps.LatLng(
                          _currentLocation!.latitude,
                          _currentLocation!.longitude,
                        ),
                        infoWindow: gmaps.InfoWindow(title: patientName),
                        // Custom circular avatar bitmap, not a pin — anchor at
                        // the center so the circle sits exactly on the GPS
                        // point rather than pointing at it from below.
                        anchor: const Offset(0.5, 0.5),
                        icon: _patientIcon ??
                            gmaps.BitmapDescriptor.defaultMarkerWithHue(
                              gmaps.BitmapDescriptor.hueAzure,
                            ),
                      ),
                    // Home and other safe places added on the add-patient
                    // form — green for home, orange for the rest, so a
                    // caregiver can tell "their own house" apart from "a
                    // place they visit" at a glance.
                    for (final place in _places)
                      gmaps.Marker(
                        markerId: gmaps.MarkerId('place_${place['cluster_id']}'),
                        position: gmaps.LatLng(
                          (place['latitude'] as num).toDouble(),
                          (place['longitude'] as num).toDouble(),
                        ),
                        infoWindow: gmaps.InfoWindow(
                          title: place['place_name'] as String? ?? 'Unnamed place',
                        ),
                        icon: gmaps.BitmapDescriptor.defaultMarkerWithHue(
                          place['is_home'] == true
                              ? gmaps.BitmapDescriptor.hueGreen
                              : gmaps.BitmapDescriptor.hueOrange,
                        ),
                      ),
                  },
                ),
                if (_currentLocation == null)
                  Container(
                    color: Colors.black.withValues(alpha: 0.05),
                    child: const Center(
                      child: Padding(
                        padding: EdgeInsets.all(16),
                        child: Text(
                          'Waiting for location...',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w600,
                            color: Colors.black54,
                          ),
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          // Persistent info panel below the map, styled like a fixed bottom
          // sheet — keeps the map maximally large (per the report's C-2
          // full-screen map intent) while still surfacing every status field
          // without scrolling or extra taps.
          Container(
            width: double.infinity,
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 24),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: const BorderRadius.only(
                topLeft: Radius.circular(24),
                topRight: Radius.circular(24),
              ),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.08),
                  blurRadius: 12,
                  offset: const Offset(0, -4),
                ),
              ],
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Semantics(
                      label: _riskScore != null
                          ? 'Risk score ${_riskScore!.toStringAsFixed(0)} out of 100, ${_riskLevelLabel(_riskLevel)} risk'
                          : 'Risk score not available',
                      child: Container(
                        width: 64,
                        height: 64,
                        decoration: BoxDecoration(
                          color: riskBg,
                          shape: BoxShape.circle,
                          border: Border.all(color: riskColor, width: 3),
                        ),
                        alignment: Alignment.center,
                        child: Text(
                          _riskScore != null ? _riskScore!.toStringAsFixed(0) : '--',
                          style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: riskColor),
                        ),
                      ),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            _riskScore != null
                                ? '${_riskLevelLabel(_riskLevel)} risk'
                                : 'Risk score not available',
                            style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: riskColor),
                          ),
                          const SizedBox(height: 2),
                          Text(
                            _riskScore != null ? 'Updated${_riskAgeLabel()}' : 'Waiting for first calculation',
                            style: TextStyle(fontSize: 13, color: Colors.grey[600]),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 20),
                const Divider(height: 1),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: _StatTile(
                        icon: statusIcon,
                        iconColor: statusColor,
                        label: 'Status',
                        value: statusLabel,
                        valueColor: statusColor,
                      ),
                    ),
                    Expanded(
                      child: _StatTile(
                        icon: Icons.social_distance_rounded,
                        iconColor: Colors.blueGrey,
                        label: 'From home',
                        value: distanceInMeters != null
                            ? '${distanceInMeters.toStringAsFixed(0)} m'
                            : 'Not set',
                        valueColor: Colors.black87,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                Semantics(
                  label: _lastUpdatedLabel(),
                  child: Row(
                    children: [
                      Icon(Icons.access_time_rounded, size: 18,
                          color: fixIsStale ? Colors.orange[800] : Colors.grey[600]),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          _lastUpdatedLabel(),
                          style: TextStyle(
                            fontSize: 14,
                            color: fixIsStale ? Colors.orange[800] : Colors.grey[600],
                            fontWeight: FontWeight.w500,
                          ),
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
    );
  }
}

/// One label+value cell in the bottom info panel — kept as a small stateless
/// widget so status/distance stay visually identical and both get the same
/// tap-target-free, screen-reader-friendly treatment.
class _StatTile extends StatelessWidget {
  final IconData icon;
  final Color iconColor;
  final String label;
  final String value;
  final Color valueColor;

  const _StatTile({
    required this.icon,
    required this.iconColor,
    required this.label,
    required this.value,
    required this.valueColor,
  });

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '$label: $value',
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: iconColor, size: 22),
          const SizedBox(width: 8),
          Flexible(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label,
                  style: TextStyle(fontSize: 12, color: Colors.grey[600], fontWeight: FontWeight.w500),
                ),
                const SizedBox(height: 2),
                Text(
                  value,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: valueColor),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
