import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import '../../services/api_client.dart';
import 'track_screen.dart';

/// Module 4 — reads GET /api/search-area/{id}. NEVER call this automatically
/// or on a timer: it's heavy (10k Monte Carlo paths) and side-effecting (can
/// write a gps_loss alert + push). Only fires on an explicit button press.
class FindPatientScreen extends StatefulWidget {
  final Map<String, dynamic> patient;
  const FindPatientScreen({super.key, required this.patient});

  @override
  State<FindPatientScreen> createState() => _FindPatientScreenState();
}

class _FindPatientScreenState extends State<FindPatientScreen> {
  final TextEditingController _minutesController = TextEditingController(text: '25');
  final TextEditingController _lastLatController = TextEditingController();
  final TextEditingController _lastLngController = TextEditingController();

  bool _loading = false;
  String? _error;
  Map<String, dynamic>? _result;

  @override
  void dispose() {
    _minutesController.dispose();
    _lastLatController.dispose();
    _lastLngController.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    setState(() {
      _loading = true;
      _error = null;
      _result = null;
    });

    final query = <String, dynamic>{
      'time_missing_minutes': _minutesController.text.trim(),
    };
    if (_lastLatController.text.trim().isNotEmpty && _lastLngController.text.trim().isNotEmpty) {
      query['last_lat'] = _lastLatController.text.trim();
      query['last_lng'] = _lastLngController.text.trim();
    }

    try {
      final res = await apiGet('/api/search-area/${widget.patient['id']}', queryParams: query);
      if (res.statusCode != 200) {
        setState(() => _error = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้');
        return;
      }
      setState(() => _result = jsonDecode(res.body) as Map<String, dynamic>);
    } catch (_) {
      setState(() => _error = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('ค้นหา ${widget.patient['name']}')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'กดค้นหาเมื่อหาผู้ป่วยไม่เจอเท่านั้น — การคำนวณนี้หนักมาก ห้ามกดซ้ำๆ',
              style: TextStyle(color: Colors.red, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _minutesController,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                border: OutlineInputBorder(),
                labelText: 'หายไปกี่นาทีแล้ว',
              ),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _lastLatController,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                    decoration: const InputDecoration(
                      border: OutlineInputBorder(),
                      labelText: 'Lat ที่เห็นครั้งล่าสุด (ถ้ามี)',
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: TextField(
                    controller: _lastLngController,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                    decoration: const InputDecoration(
                      border: OutlineInputBorder(),
                      labelText: 'Lng ที่เห็นครั้งล่าสุด (ถ้ามี)',
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: _loading ? null : _search,
                style: ElevatedButton.styleFrom(backgroundColor: Colors.red, minimumSize: const Size(0, 48)),
                child: _loading
                    ? const CircularProgressIndicator(color: Colors.white)
                    : const Text('ค้นหาตอนนี้', style: TextStyle(color: Colors.white)),
              ),
            ),
            const SizedBox(height: 16),
            if (_error != null) Text(_error!, style: const TextStyle(color: Colors.red)),
            if (_result != null) Expanded(child: _buildResult(_result!)),
          ],
        ),
      ),
    );
  }

  Widget _buildResult(Map<String, dynamic> result) {
    final status = result['status'] as String? ?? 'no_data';

    if (status == 'gps_active') {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Text('GPS ของผู้ป่วยยังส่งเข้ามาอยู่ — ไม่ต้องค้นหา', textAlign: TextAlign.center),
            const SizedBox(height: 12),
            ElevatedButton(
              onPressed: () => Navigator.pushReplacement(
                context,
                MaterialPageRoute(builder: (context) => TrackScreen(patient: widget.patient)),
              ),
              child: const Text('ไปหน้าติดตามตำแหน่งแทน'),
            ),
          ],
        ),
      );
    }

    if (status == 'no_data') {
      return const Center(
        child: Text(
          'ไม่มีพิกัดให้คำนวณเลย — กรอกจุดที่เห็นผู้ป่วยครั้งล่าสุดด้านบนแล้วค้นหาใหม่',
          textAlign: TextAlign.center,
        ),
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
        const SizedBox(height: 12),
        Text('รัศมีค้นหา: $adjustedRadius ม. (จากเดิม $searchRadius ม.)',
            style: const TextStyle(fontWeight: FontWeight.w600)),
        if (adjustmentReason != null) Text(adjustmentReason, style: TextStyle(color: Colors.grey[600])),
        const SizedBox(height: 8),
        Row(
          children: [
            _legendDot(Colors.red, 'โอกาสสูง'),
            const SizedBox(width: 12),
            _legendDot(Colors.orange, 'โอกาสกลาง'),
            const SizedBox(width: 12),
            _legendDot(Colors.yellow[700]!, 'โอกาสต่ำ'),
          ],
        ),
        const SizedBox(height: 12),
        const Text('ที่ควรไปดูก่อน', style: TextStyle(fontWeight: FontWeight.w600)),
        if (targets.isEmpty)
          const Text('ไม่มีสถานที่คุ้นเคยให้แนะนำ — ผู้ป่วยยังไม่มีหมุด')
        else
          ...targets.map((t) => Card(
                child: ListTile(
                  leading: const Icon(Icons.place),
                  title: Text(t['name'] as String? ?? 'ไม่มีชื่อ'),
                ),
              )),
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
        infoWindow: const gmaps.InfoWindow(title: 'จุดที่เห็นล่าสุด'),
      ));
    }
    for (final t in targets) {
      markers.add(gmaps.Marker(
        markerId: gmaps.MarkerId('target_${t['name']}_${t['latitude']}'),
        position: gmaps.LatLng((t['latitude'] as num).toDouble(), (t['longitude'] as num).toDouble()),
        infoWindow: gmaps.InfoWindow(title: t['name'] as String? ?? 'ไม่มีชื่อ'),
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
