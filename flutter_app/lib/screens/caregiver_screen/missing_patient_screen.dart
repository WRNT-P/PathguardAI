import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import '../../services/api_client.dart';
import '../../utils/patient_marker.dart';

/// Module 4 — the search area for a patient nobody can find.
///
/// Opens two ways, and the difference matters to everything below.
///
/// **Automatic**, off an unresolved `gps_loss` alert (same trigger pattern as
/// SosAlertScreen): the system noticed first. GET /api/search-area/{id} is
/// called with no query params, letting the backend use its own defaults —
/// a caregiver who just opened the app to a "we can't find them" screen
/// shouldn't have to type anything before seeing a summary.
///
/// **Manual**, from the track screen: the caregiver noticed first. That case
/// was unreachable until now, and it is the commoner one — a phone reporting
/// GPS perfectly well from the pocket of someone who is not answering their
/// door raises no alert at all, so the automatic path never fires. Opening it
/// by hand passes the last known coordinates and how long they have been
/// gone, which is what the backend needs to search anyway (see
/// `search_area.py`: supplied coordinates override the "GPS is active, no
/// search needed" early return, and deliberately do NOT raise a false
/// gps_loss alert the rest of the family would be woken by).
///
/// Never polled — one call on open. The 10 s poll is on the *alert*, to know
/// when it's resolved, and only exists on the automatic path: a manual search
/// has no alert to watch and must not close itself out from under the person
/// using it.
class MissingPatientScreen extends StatefulWidget {
  final Map<String, dynamic> patient;

  /// The `gps_loss` alert this screen was opened for, or null when a
  /// caregiver opened it themselves.
  final Map<String, dynamic>? alert;

  /// Where the patient was last seen, sent only on the manual path — the
  /// automatic one lets the backend read its own latest row.
  final double? lastLat;
  final double? lastLng;

  /// How long they have been gone, as the caregiver reports it. Feeds the
  /// search radius directly (speed × time), which is why it is asked rather
  /// than assumed; the backend falls back to a cautious 25 minutes.
  final int? minutesMissing;

  const MissingPatientScreen({
    super.key,
    required this.patient,
    required this.alert,
  })  : lastLat = null,
        lastLng = null,
        minutesMissing = null;

  /// Started by a caregiver, not by an alert.
  const MissingPatientScreen.manual({
    super.key,
    required this.patient,
    this.lastLat,
    this.lastLng,
    this.minutesMissing,
  }) : alert = null;

  @override
  State<MissingPatientScreen> createState() => _MissingPatientScreenState();
}

class _MissingPatientScreenState extends State<MissingPatientScreen> {
  late Map<String, dynamic>? _alert = widget.alert;
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _result;
  Timer? _alertPoll;
  gmaps.BitmapDescriptor? _patientIcon;
  /// Same guard as sos_alert_screen: the close button and the 10s poll can
  /// both decide to leave, and two pops take the patient list with them and
  /// leave a black screen.
  bool _leaving = false;

  bool get _isManual => widget.alert == null;

  @override
  void initState() {
    super.initState();
    _search();
    _loadPatientIcon();
    // Nothing to watch on a manual search, and watching nothing would mean
    // `firstWhere ... orElse: _alert` on a null alert.
    if (!_isManual) {
      _alertPoll = Timer.periodic(const Duration(seconds: 10), (_) => _checkResolved());
    }
  }

  @override
  void dispose() {
    _alertPoll?.cancel();
    super.dispose();
  }

  /// Same avatar the track and navigation maps draw, so the patient looks like
  /// one person across every screen a caregiver moves between mid-search.
  Future<void> _loadPatientIcon() async {
    final icon = await buildPatientMarkerIcon(widget.patient['profileImage'] as File?);
    if (mounted) setState(() => _patientIcon = icon);
  }

  /// The one way out, so no two exits can fire.
  void _close() {
    if (_leaving) return;
    _leaving = true;
    _alertPoll?.cancel();
    if (mounted) Navigator.of(context).pop();
  }

  Future<void> _checkResolved() async {
    if (_leaving) return;
    final current = _alert;
    if (current == null) return;
    try {
      final res = await apiGet('/api/patients/${widget.patient['id']}/alerts?limit=100');
      if (res.statusCode != 200) return;
      final alerts = (jsonDecode(res.body)['alerts'] as List).cast<Map<String, dynamic>>();
      final updated = alerts.firstWhere((a) => a['id'] == current['id'], orElse: () => current);
      if (updated['resolved'] == true) {
        _close();
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
      // Sent only on the manual path. Coordinates are what let the search run
      // at all while the phone is still reporting — without them the backend
      // answers "GPS is active, no search needed", which is true of the phone
      // and useless about the person.
      final params = <String, String>{
        if (widget.lastLat != null) 'last_lat': '${widget.lastLat}',
        if (widget.lastLng != null) 'last_lng': '${widget.lastLng}',
        if (widget.minutesMissing != null)
          'time_missing_minutes': '${widget.minutesMissing}',
      };
      final res = await apiGet(
        '/api/search-area/${widget.patient['id']}',
        queryParams: params.isEmpty ? null : params,
      );
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
      backgroundColor: Colors.red[50],
      appBar: AppBar(
        backgroundColor: Colors.red,
        foregroundColor: Colors.white,
        title: Text(_isManual
            ? 'ค้นหา ${widget.patient['name']}'
            : 'หายตัว — ${widget.patient['name']}'),
        automaticallyImplyLeading: false,
        actions: [
          IconButton(icon: const Icon(Icons.close), onPressed: _close),
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
      // On the automatic path this is good news: the signal came back between
      // the alert firing and the caregiver opening the app.
      //
      // On the manual path it means the coordinates never got sent — the
      // track screen had no fix to hand over yet. Saying "no need to search"
      // to somebody who opened this screen because they cannot find a person
      // would be answering about the phone, so it offers the retry instead.
      if (!_isManual) {
        return const Center(
          child: Text('GPS ของผู้ป่วยกลับมาส่งตำแหน่งแล้ว ไม่ต้องค้นหา', textAlign: TextAlign.center),
        );
      }
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'โทรศัพท์ยังส่งตำแหน่งอยู่ จึงไม่มีจุดที่เห็นล่าสุดให้ใช้ค้นหา',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              const Text(
                'เปิดแผนที่ รอให้ตำแหน่งโหลด แล้วเริ่มค้นหาใหม่',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 16),
              FilledButton(onPressed: _search, child: const Text('ลองอีกครั้ง')),
            ],
          ),
        ),
      );
    }

    if (status == 'no_data') {
      return const Center(
        child: Text('ข้อมูลตำแหน่งยังไม่พอสำหรับคำนวณพื้นที่ค้นหา', textAlign: TextAlign.center),
      );
    }

    final searchRadius = result['search_radius_meters'];
    final adjustedRadius = result['adjusted_radius_meters'];
    final adjustmentReason = result['adjustment_reason'] as String?;
    final speedUsed = (result['speed_ms_used'] as num?)?.toDouble();
    final speedLabel = speedUsed == null
        ? null
        : 'ความเร็วที่ใช้คำนวณ: ${(speedUsed * 3.6).toStringAsFixed(1)} กม./ชม. '
            '(${switch (result['speed_source'] as String?) {
              'last_fix' => 'วัดได้จากตำแหน่งล่าสุด',
              'learned' => 'ค่าเฉลี่ยที่ระบบเรียนรู้จากผู้ป่วย',
              'override' => 'ค่าที่ระบุเอง',
              _ => 'ค่ามาตรฐาน ยังไม่รู้ความเร็วของผู้ป่วย',
            }})';
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
              Text('รัศมีค้นหา: $adjustedRadius ม. (จากเดิม $searchRadius ม.)',
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              // The radius is speed × time, so the caregiver should be able to
              // see the time it was worked out from — a radius quoted with no
              // basis is a number they cannot sanity-check or correct.
              if (widget.minutesMissing != null)
                Text('คำนวณจากเวลาที่หายไป ${widget.minutesMissing} นาที',
                    style: TextStyle(color: Colors.grey[600])),
              // The other half of speed x time. A radius built on an assumed
              // pace and one built on this patient's measured pace are
              // different claims, and only the caregiver can judge which to
              // trust against what they know about today.
              if (speedLabel != null)
                Text(speedLabel, style: TextStyle(color: Colors.grey[600])),
              if (adjustmentReason != null)
                Text(adjustmentReason, style: TextStyle(color: Colors.grey[600])),
              const SizedBox(height: 8),
              Row(
                children: [
                  _legendDot(Colors.red, 'โอกาสสูง'),
                  const SizedBox(width: 12),
                  _legendDot(Colors.orange, 'โอกาสปานกลาง'),
                  const SizedBox(width: 12),
                  _legendDot(Colors.yellow[700]!, 'โอกาสต่ำ'),
                ],
              ),
              const SizedBox(height: 12),
              const Text('ตรวจดูสถานที่เหล่านี้ก่อน', style: TextStyle(fontWeight: FontWeight.w600)),
              if (targets.isEmpty)
                const Text('ยังไม่มีสถานที่คุ้นเคยให้แนะนำ เพราะยังไม่ได้ปักหมุดสถานที่ของผู้ป่วย')
              else
                ...targets.map((t) => Card(
                      child: ListTile(
                        leading: const Icon(Icons.place),
                        title: Text(t['name'] as String? ?? 'ไม่มีชื่อ'),
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
        icon: _patientIcon ??
            gmaps.BitmapDescriptor.defaultMarkerWithHue(gmaps.BitmapDescriptor.hueBlue),
        // The avatar is a circle, so it has to sit centred on the coordinate.
        // A marker's default anchor is the tip of a teardrop, which would put
        // the last known position half a marker below where it really was.
        anchor: _patientIcon == null ? const Offset(0.5, 1.0) : const Offset(0.5, 0.5),
        infoWindow: const gmaps.InfoWindow(title: 'ตำแหน่งที่เห็นล่าสุด'),
      ));
    }
    for (final t in targets) {
      markers.add(gmaps.Marker(
        markerId: gmaps.MarkerId('target_${t['name']}_${t['latitude']}'),
        position: gmaps.LatLng((t['latitude'] as num).toDouble(), (t['longitude'] as num).toDouble()),
        // Violet, not the default red: red/orange/yellow are spoken for by the
        // probability zones below, and a warm pin reads as "likely here" when
        // it actually means "a place worth checking". Green would collide with
        // the red zone for the commonest colour blindness.
        icon: gmaps.BitmapDescriptor.defaultMarkerWithHue(gmaps.BitmapDescriptor.hueViolet),
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
