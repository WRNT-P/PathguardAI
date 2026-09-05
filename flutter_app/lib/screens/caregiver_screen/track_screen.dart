import 'dart:convert';
import 'dart:ui' as ui;
import 'package:collection/collection.dart';
import '../../services/api_client.dart';

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
  String _status = 'stationary';
  double? _riskScore;
  String? _riskLevel;
  DateTime? _riskCalculatedAt;
  List<Map<String, dynamic>> _places = [];
  gmaps.GoogleMapController? _mapController;
  gmaps.BitmapDescriptor? _patientIcon;

  Future<Map<String, dynamic>?> _fetchLatestTrackPoint() async {
    final res = await apiGet('/api/patients/${widget.patient['id']}/track', queryParams: {'hours': '6'});
    if (res.statusCode != 200) return null;
    final data = jsonDecode(res.body);
    final points = data['points'] as List;
    if (points.isEmpty) return null;
    return points.last as Map<String, dynamic>;
  }

  Future<List<dynamic>> _fetchAlerts() async {
    final res = await apiGet('/api/patients/${widget.patient['id']}/alerts');
    if (res.statusCode != 200) return [];
    final data = jsonDecode(res.body);
    return data['alerts'] as List;
  }

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
    final icon = await _buildPatientIcon(widget.patient['profileImage'] as File?);
    if (mounted) setState(() => _patientIcon = icon);
  }

  Future<gmaps.BitmapDescriptor> _buildPatientIcon(File? profileImage) async {
    const double size = 64;
    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder, const Rect.fromLTWH(0, 0, size, size));
    const center = Offset(size / 2, size / 2);
    const radius = size / 2;

    var drewPhoto = false;
    if (profileImage != null) {
      try {
        final bytes = await profileImage.readAsBytes();
        final codec = await ui.instantiateImageCodec(
          bytes,
          targetWidth: size.toInt(),
          targetHeight: size.toInt(),
        );
        final frame = await codec.getNextFrame();
        canvas.save();
        canvas.clipPath(ui.Path()..addOval(Rect.fromCircle(center: center, radius: radius - 4)));
        paintImage(
          canvas: canvas,
          rect: Rect.fromCircle(center: center, radius: radius - 4),
          image: frame.image,
          fit: BoxFit.cover,
        );
        canvas.restore();
        drewPhoto = true;
      } catch (_) {
        drewPhoto = false;
      }
    }

    if (!drewPhoto) {
      canvas.drawCircle(center, radius - 4, Paint()..color = Colors.blue);
      final iconPainter = TextPainter(textDirection: TextDirection.ltr)
        ..text = TextSpan(
          text: String.fromCharCode(Icons.person.codePoint),
          style: TextStyle(
            fontSize: radius,
            fontFamily: Icons.person.fontFamily,
            package: Icons.person.fontPackage,
            color: Colors.white,
          ),
        )
        ..layout();
      iconPainter.paint(
        canvas,
        center - Offset(iconPainter.width / 2, iconPainter.height / 2),
      );
    }

    canvas.drawCircle(
      center,
      radius - 0.5,
      Paint()
        ..color = Colors.white
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1,
    );

    final picture = recorder.endRecording();
    final image = await picture.toImage(size.toInt(), size.toInt());
    final byteData = await image.toByteData(format: ui.ImageByteFormat.png);
    // Without an explicit logical size, the platform draws the PNG's raw
    // pixels 1:1 against device pixels — on a high-DPI phone that makes a
    // 64x64 bitmap render far larger on-screen than a 64dp widget would.
    return gmaps.BitmapDescriptor.bytes(
      byteData!.buffer.asUint8List(),
      width: 36,
      height: 36,
    );
  }

  @override
  void initState() {
    super.initState();
    _loadPatientIcon();
    _fetchPlaces();
    _timer = Timer.periodic(const Duration(seconds: 15), (timer) async {
      final point = await _fetchLatestTrackPoint();
      final alerts = await _fetchAlerts();
      final risk = await _fetchLatestRisk();
      if (!mounted) return;

      final activeAlert = alerts
          .cast<Map<String, dynamic>>()
          .where((a) => a['resolved'] == false)
          .firstOrNull;

      setState(() {
        if (point != null) {
          _currentLocation = LatLng(
            (point['latitude'] as num).toDouble(),
            (point['longitude'] as num).toDouble(),
          );
          final recordedAt = point['recorded_at'] as String?;
          if (recordedAt != null) {
            _lastUpdated = DateTime.parse(recordedAt).toLocal();
          }
        }
        _status = activeAlert != null ? 'traveling' : 'stationary';

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

  @override
  void dispose() {
    _timer?.cancel();
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

  @override
  Widget build(BuildContext context) {
    final profileImage = widget.patient['profileImage'] as File?;
    final patientName = widget.patient['name'] as String? ?? 'Patient';
    final isTraveling = _status == 'traveling';
    final statusColor = isTraveling ? Colors.orange[800]! : Colors.green[700]!;
    final statusIcon = isTraveling ? Icons.directions_walk_rounded : Icons.home_rounded;
    final statusLabel = isTraveling ? 'Traveling' : 'At safe place';
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
                  label: _lastUpdated != null
                      ? 'Location last updated at ${_formatTime(_lastUpdated!)}'
                      : 'Waiting for location',
                  child: Row(
                    children: [
                      Icon(Icons.access_time_rounded, size: 18, color: Colors.grey[600]),
                      const SizedBox(width: 6),
                      Text(
                        _lastUpdated != null
                            ? 'Location last updated ${_formatTime(_lastUpdated!)}'
                            : 'Waiting for location...',
                        style: TextStyle(fontSize: 14, color: Colors.grey[600], fontWeight: FontWeight.w500),
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
