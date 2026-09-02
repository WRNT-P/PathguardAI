import 'dart:convert';
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

  Future<LatLng?> _fetchLatestLocation() async {
    final res = await apiGet('/api/patients/${widget.patient['id']}/track', queryParams: {'hours': '6'});
    final data = jsonDecode(res.body);
    final points = data['points'] as List;
    if (points.isEmpty) return null;
    final last = points.last;
    return LatLng(last['latitude'], last['longitude']);
  }

  Future<List<dynamic>> _fetchAlerts() async {
    final res = await apiGet('/api/patients/${widget.patient['id']}/alerts');
    final data = jsonDecode(res.body);
    return data ['alerts'] as List;
  }

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 15), (timer) async {
      final location = await _fetchLatestLocation();
      final alerts = await _fetchAlerts();
      final activeAlert = alerts
          .cast<Map<String, dynamic>>()
          .where((a) => a['resolved'] == false)
          .firstOrNull;
      setState(() {
        _currentLocation = location ?? _currentLocation;
        _lastUpdated = DateTime.now();
        _status = activeAlert != null ? 'traveling' : 'stationary';
        _riskScore = activeAlert == null
            ? null
            : activeAlert['severity'] == 'critical'
                ? 90.0
                : activeAlert['severity'] == 'warning'
                    ? 60.0
                    : 20.0;
      });
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final profileImage = widget.patient['profileImage'] as File?;
    final isTraveling = _status == 'traveling';
    final statusColor = isTraveling ? Colors.orange : Colors.green;
    final statusIcon = isTraveling ? Icons.directions_walk : Icons.home;
    final statusLabel = isTraveling ? 'Traveling' : 'Stationary';
    final homePlace = widget.patient['home'] as ParsedLocation?;
    double? distanceInMeters;
    final riskLevel = _riskScore == null
    ? null
    : _riskScore! > 80
      ? 'High'
      : _riskScore! >= 50
        ? 'Medium'
        : 'Low';
    final riskColor = riskLevel == 'High'
      ? Colors.red
      : riskLevel == 'Medium'
        ? Colors.orange
        : Colors.green;

    if (homePlace != null && _currentLocation != null) {
      final homeLatLng = LatLng(homePlace.latitude, homePlace.longitude);
      distanceInMeters = const Distance().as(LengthUnit.Meter, homeLatLng, _currentLocation!);
    }
    
    return Scaffold(
      appBar: AppBar(
        backgroundColor: Colors.grey[300],
        title: Row(
          children: [
            CircleAvatar(
              radius: 20, // Adjust size
              backgroundColor: Colors.grey[300],
              backgroundImage: profileImage != null ? FileImage(profileImage) : null,
              child: profileImage == null ? Icon(Icons.person,size: 35, color: Colors.grey[800]): null,
            ),
            const SizedBox(width: 12),
            Text(
              widget.patient['name'],
              style: const TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.bold,
                color: Colors.black87,
              ),
            ),
          ],
        ),
      ),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            height: 300,
            child: gmaps.GoogleMap(
              initialCameraPosition: const gmaps.CameraPosition(
                target: gmaps.LatLng(13.7563, 100.5018),
                zoom: 15.0,
              ),
              markers: _currentLocation != null
                ? {
                    gmaps.Marker(
                      markerId: const gmaps.MarkerId('patient'),
                      position: gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude),
                    ),
                  }
                : {},
            ),
          ),
          Padding(
            padding: const EdgeInsets.only(left: 16, top: 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children:[
                Text(
                  _riskScore != null
                    ? 'Risk Score: ${_riskScore!.toStringAsFixed(0)}/100 ($riskLevel)'
                    : 'Risk score not available',
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: riskColor),
                ),
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      'Status: ',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    Icon(statusIcon, color: statusColor, size: 16),
                    const SizedBox(width: 4),
                    Text(statusLabel, style: TextStyle(color: statusColor, fontWeight: FontWeight.w600)),
                  ]
                ),
                Text(
                _lastUpdated != null
                  ? 'Last updated: ${_lastUpdated!.hour}:${_lastUpdated!.minute.toString().padLeft(2, '0')}'
                  : 'Waiting for location...',
                  style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                  )
                ),
                Text(
                  distanceInMeters != null
                  ? 'Distance: ${distanceInMeters.toStringAsFixed(0)}m from home'
                  : 'Safe place not set',
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)
                ),
              ]
            )
          ),
        ],
      ),
    );
  }
}