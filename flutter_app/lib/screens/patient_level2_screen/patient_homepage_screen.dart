import 'package:flutter/material.dart';
import 'navigation_screen.dart';
import '../../services/sos_service.dart';
import '../../services/trip_approval_service.dart';
import '../../services/gps_reporter.dart';
import 'dart:convert';
import '../../services/api_client.dart';
import '../../services/session.dart';

enum _ScreenState { picking, waitingApproval, rejected, sosActive }

class PatientHomePageScreen extends StatefulWidget {
  final String? patientName;
  const PatientHomePageScreen({super.key, this.patientName});

  @override
  State<PatientHomePageScreen> createState() => _PatientHomePageScreenState();
}

class _PatientHomePageScreenState extends State<PatientHomePageScreen> {
  List<Map<String,dynamic>> recommendedPlaces = [];
  bool _loadingPlaces = true;

  Future<void> _loadRecommendations() async {
    final patientId = Session.instance.patientId;
    if(patientId == null) return;

    final response = await apiGet('/api/recommendation/$patientId');
    if(!mounted) return;

    if (response.statusCode != 200) {
      setState(() => _loadingPlaces = false);
      return;
    }

    final body = jsonDecode(response.body);
    final recommendations = body['recommendations'] as List;

    setState(() {
      recommendedPlaces = recommendations
          .where((r) => r['place_name'] != null)
          .map((r) => {
                'name': r['place_name'] as String,
                'lat': (r['latitude'] as num).toDouble(),
                'lng': (r['longitude'] as num).toDouble(),
              })
          .toList();
      _loadingPlaces = false;
    });
   }

  @override
  void initState() {
    super.initState();
    startGpsReporting();
    _loadRecommendations();
  }



  _ScreenState _state = _ScreenState.picking;
  Map<String, dynamic>? _selectedPlace;
  bool _sosSending = false;

  @override
  void dispose() {
    stopGpsReporting();
    super.dispose();
  }

  Future<void> _handleSelectPlace(Map<String, dynamic> place) async {
    setState(() {
      _selectedPlace = place;
      _state = _ScreenState.waitingApproval;
    });

    final approved = await requestTripApproval(
      patientName: widget.patientName ?? 'patient',
      place: place,
    );

    if (!mounted) return;

    if (approved) {
      Navigator.push(
        context,
        MaterialPageRoute(builder: (context) => NavigationScreen(place: place)),
      ).then((_) {
        if (!mounted) return;
        setState(() {
          _state = _ScreenState.picking;
          _selectedPlace = null;
        });
      });
    } else {
      setState(() {
        _state = _ScreenState.rejected;
      });
    }
  }

  Future<void> _handleSOS() async {
    setState(() {
      _sosSending = true;
    });

    await triggerSOS();

    if (!mounted) return;
    setState(() {
      _sosSending = false;
      _state = _ScreenState.sosActive;
    });
  }

  Widget _buildStayPutState() {
    return const Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.favorite, size: 120, color: Colors.red),
          SizedBox(height: 24),
          Text(
            'Stay where you are.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 28, fontWeight: FontWeight.bold),
          ),
          SizedBox(height: 12),
          Text(
            'Help is coming.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }

  Widget _buildSosButton() {
    return SizedBox(
      width: 96,
      height: 96,
      child: FloatingActionButton(
        onPressed: _sosSending ? null : _handleSOS,
        backgroundColor: Colors.red,
        shape: const CircleBorder(),
        child: const Text(
          'SOS',
          style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 18),
        ),
      ),
    );
  }

  Widget _buildWaitingState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.hourglass_top, size: 120, color: Colors.blue),
          const SizedBox(height: 24),
          Text(
            'Asking your caregiver about ${_selectedPlace?['name'] ?? 'this trip'}...',
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }

  Widget _buildRejectedState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.info_outline, size: 120, color: Colors.orange),
          const SizedBox(height: 24),
          const Text(
            'Let\'s pick something else',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 24, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 24),
          ElevatedButton(
            onPressed: () {
              setState(() {
                _state = _ScreenState.picking;
                _selectedPlace = null;
              });
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: Colors.blue,
              minimumSize: const Size(200, 56),
            ),
            child: const Text('OK', style: TextStyle(color: Colors.white, fontSize: 18)),
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.hourglass_empty, size: 120, color: Colors.grey[500]),
          const SizedBox(height: 24),
          const Text(
            'Waiting for your caregiver to add places',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }

  Widget _buildPlaceTile(Map<String, dynamic> place) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: InkWell(
        onTap: () => _handleSelectPlace(place),
        borderRadius: BorderRadius.circular(16),
        child: Container(
          height: 120,
          width: double.infinity,
          decoration: BoxDecoration(
            color: Colors.grey[200],
            borderRadius: BorderRadius.circular(16),
          ),
          child: Row(
            children: [
              const SizedBox(width: 20),
              const Icon(Icons.place, size: 48, color: Colors.blue),
              const SizedBox(width: 20),
              Text(
                place['name'],
                style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildPickingState() {
    if (_loadingPlaces) {
      return const Center(child: CircularProgressIndicator());
    }
    if (recommendedPlaces.isEmpty) {
      return _buildEmptyState();
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Choose the place you want to go.',
          style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 20),
        ...recommendedPlaces.map(_buildPlaceTile),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final Widget content;
    switch (_state) {
      case _ScreenState.picking:
        content = _buildPickingState();
        break;
      case _ScreenState.waitingApproval:
        content = _buildWaitingState();
        break;
      case _ScreenState.rejected:
        content = _buildRejectedState();
        break;
      case _ScreenState.sosActive:
        content = _buildStayPutState();
        break;
    }

    return Scaffold(
      appBar: AppBar(
        title: Text('Hello, ${widget.patientName ?? "Friend"}'),
      ),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: content,
      ),
      floatingActionButtonLocation: FloatingActionButtonLocation.centerFloat,
      floatingActionButton: _buildSosButton(),
    );
  }
}
