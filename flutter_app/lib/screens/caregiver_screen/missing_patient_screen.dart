import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import '../../services/api_client.dart';

/// Module 4 — auto-activates when a patient's GPS signal is confirmed lost
/// (an unresolved gps_lost alert), same trigger pattern as SosAlertScreen.
/// No manual form: GET /api/search-area/{id} is called once with no query
/// params, letting the backend use its own defaults (25 min missing, last
/// recorded position) — a caregiver who just opened the app to a "we can't
/// find them" screen shouldn't have to type anything before seeing a summary.
/// Still never polled — one call on open, then just polls the *alert* to
/// know when it's resolved (GPS came back), not the heavy search itself.
class MissingPatientScreen extends StatefulWidget {
  final Map<String, dynamic> patient;
  final Map<String, dynamic> alert;
  const MissingPatientScreen({super.key, required this.patient, required this.alert});

  @override
  State<MissingPatientScreen> createState() => _MissingPatientScreenState();
}

class _MissingPatientScreenState extends State<MissingPatientScreen> {
  late Map<String, dynamic> _alert = widget.alert;
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _result;
  Timer? _alertPoll;

  @override
  void initState() {
    super.initState();
    _search();
    _alertPoll = Timer.periodic(const Duration(seconds: 10), (_) => _checkResolved());
  }

  @override
  void dispose() {
    _alertPoll?.cancel();
    super.dispose();
  }

  Future<void> _checkResolved() async {
    try {
      final res = await apiGet('/api/patients/${widget.patient['id']}/alerts');
      if (res.statusCode != 200) return;
      final alerts = (jsonDecode(res.body)['alerts'] as List).cast<Map<String, dynamic>>();
      final updated = alerts.firstWhere((a) => a['id'] == _alert['id'], orElse: () => _alert);
      if (updated['resolved'] == true && mounted) {
        Navigator.of(context).pop();
        return;
      }
      if (mounted) setState(() => _alert = updated);
    } catch (_) {
    }
  }

  Future<void> _search() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await apiGet('/api/search-area/${widget.patient['id']}');
      if (res.statusCode != 200) {
        setState(() => _error = 'Could not connect to the server');
        return;
      }
      setState(() => _result = jsonDecode(res.body) as Map<String, dynamic>);
    } catch (_) {
      setState(() => _error = 'Could not connect to the server');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.red[50],
      appBar: AppBar(
        backgroundColor: Colors.red,
        foregroundColor: Colors.white,
        title: Text('Missing — ${widget.patient['name']}'),
        automaticallyImplyLeading: false,
        actions: [
          IconButton(icon: const Icon(Icons.close), onPressed: () => Navigator.of(context).pop()),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _buildResult(_result!),
    );
  }

  Widget _buildResult(Map<String, dynamic> result) {
    final status = result['status'] as String? ?? 'no_data';

    if (status == 'gps_active') {
      // Shouldn't normally happen — this screen only opens off a gps_lost
      // alert — but the signal may have come back between the alert firing
      // and the caregiver opening the app.
      return const Center(
        child: Text("The patient's GPS is reporting again — no need to search", textAlign: TextAlign.center),
      );
    }

    if (status == 'no_data') {
      return const Center(
        child: Text('Not enough location data yet to calculate a search area', textAlign: TextAlign.center),
      );
    }

    final searchRadius = result['search_radius_meters'];
    final adjustedRadius = result['adjusted_radius_meters'];
    final adjustmentReason = result['adjustment_reason'] as String?;
    final targets = (result['target_locations'] as List?) ?? [];
    final lastKnown = result['last_known_location'] as Map<String, dynamic>?;
    final gridBounds = result['grid_bounds'] as Map<String, dynamic>?;

    return ListView(
      children: [
        SizedBox(
          height: 260,
          child: gmaps.GoogleMap(
            initialCameraPosition: gmaps.CameraPosition(
              target: gridBounds != null
                  ? gmaps.LatLng(
                      ((gridBounds['lat_min'] as num) + (gridBounds['lat_max'] as num)) / 2,
                      ((gridBounds['lng_min'] as num) + (gridBounds['lng_max'] as num)) / 2,
                    )
                  : gmaps.LatLng(
                      (lastKnown?['latitude'] as num?)?.toDouble() ?? 13.7563,
                      (lastKnown?['longitude'] as num?)?.toDouble() ?? 100.5018,
                    ),
              zoom: 14,
            ),
            polygons: _buildZonePolygons(result),
            markers: _buildMarkers(lastKnown, targets),
            polylines: _buildFamiliarPaths(result),
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Search radius: ${adjustedRadius}m (from ${searchRadius}m)',
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              if (adjustmentReason != null)
                Text(adjustmentReason, style: TextStyle(color: Colors.grey[600])),
              const SizedBox(height: 8),
              Row(
                children: [
                  _legendDot(Colors.red, 'High probability'),
                  const SizedBox(width: 12),
                  _legendDot(Colors.orange, 'Medium probability'),
                  const SizedBox(width: 12),
                  _legendDot(Colors.yellow[700]!, 'Low probability'),
                ],
              ),
              const SizedBox(height: 12),
              const Text('Check these places first', style: TextStyle(fontWeight: FontWeight.w600)),
              if (targets.isEmpty)
                const Text('No familiar places to suggest — the patient has no pinned places yet')
              else
                ...targets.map((t) => Card(
                      child: ListTile(
                        leading: const Icon(Icons.place),
                        title: Text(t['name'] as String? ?? 'Unnamed'),
                      ),
                    )),
            ],
          ),
        ),
      ],
    );
  }

  Widget _legendDot(Color color, String label) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 12, height: 12, color: color),
        const SizedBox(width: 4),
        Text(label, style: const TextStyle(fontSize: 12)),
      ],
    );
  }

  /// Zones can carry hundreds to thousands of points each — too many to render
  /// as individual markers without jank. Instead, take each zone's bounding
  /// box and draw it as one translucent rectangle, low zone first so higher
  /// zones layer visibly on top.
  gmaps.Polygon? _boundingBoxPolygon(String id, List? zone, Color color) {
    if (zone == null || zone.isEmpty) return null;

    double latMin = double.infinity, latMax = -double.infinity;
    double lngMin = double.infinity, lngMax = -double.infinity;
    for (final point in zone) {
      final lat = (point['latitude'] as num).toDouble();
      final lng = (point['longitude'] as num).toDouble();
      if (lat < latMin) latMin = lat;
      if (lat > latMax) latMax = lat;
      if (lng < lngMin) lngMin = lng;
      if (lng > lngMax) lngMax = lng;
    }

    return gmaps.Polygon(
      polygonId: gmaps.PolygonId(id),
      points: [
        gmaps.LatLng(latMin, lngMin),
        gmaps.LatLng(latMin, lngMax),
        gmaps.LatLng(latMax, lngMax),
        gmaps.LatLng(latMax, lngMin),
      ],
      fillColor: color.withValues(alpha: 0.35),
      strokeColor: color,
      strokeWidth: 1,
    );
  }

  Set<gmaps.Polygon> _buildZonePolygons(Map<String, dynamic> result) {
    final polygons = <gmaps.Polygon>{};
    final low = _boundingBoxPolygon('low', result['low_probability_zone'] as List?, Colors.yellow[700]!);
    final medium = _boundingBoxPolygon('medium', result['medium_probability_zone'] as List?, Colors.orange);
    final high = _boundingBoxPolygon('high', result['high_probability_zone'] as List?, Colors.red);
    if (low != null) polygons.add(low);
    if (medium != null) polygons.add(medium);
    if (high != null) polygons.add(high);
    return polygons;
  }

  Set<gmaps.Marker> _buildMarkers(Map<String, dynamic>? lastKnown, List targets) {
    final markers = <gmaps.Marker>{};
    if (lastKnown != null) {
      markers.add(gmaps.Marker(
        markerId: const gmaps.MarkerId('last_known'),
        position: gmaps.LatLng(
          (lastKnown['latitude'] as num).toDouble(),
          (lastKnown['longitude'] as num).toDouble(),
        ),
        icon: gmaps.BitmapDescriptor.defaultMarkerWithHue(gmaps.BitmapDescriptor.hueBlue),
        infoWindow: const gmaps.InfoWindow(title: 'Last seen location'),
      ));
    }
    for (final t in targets) {
      markers.add(gmaps.Marker(
        markerId: gmaps.MarkerId('target_${t['name']}_${t['latitude']}'),
        position: gmaps.LatLng((t['latitude'] as num).toDouble(), (t['longitude'] as num).toDouble()),
        infoWindow: gmaps.InfoWindow(title: t['name'] as String? ?? 'Unnamed'),
      ));
    }
    return markers;
  }

  Set<gmaps.Polyline> _buildFamiliarPaths(Map<String, dynamic> result) {
    final paths = (result['familiar_paths'] as List?) ?? [];
    final polylines = <gmaps.Polyline>{};
    for (var i = 0; i < paths.length; i++) {
      final waypoints = (paths[i]['waypoints'] as List)
          .map((wp) => gmaps.LatLng((wp[0] as num).toDouble(), (wp[1] as num).toDouble()))
          .toList();
      polylines.add(gmaps.Polyline(
        polylineId: gmaps.PolylineId('path_$i'),
        points: waypoints,
        color: Colors.blue,
        width: 3,
      ));
    }
    return polylines;
  }
}
