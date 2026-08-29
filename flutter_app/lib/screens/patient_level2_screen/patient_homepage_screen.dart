import 'package:flutter/material.dart';
import 'navigation_screen.dart';
import 'sos_contact_screen.dart';
import '../../services/sos_service.dart';
import '../../services/trip_approval_service.dart';

enum _ScreenState { picking, waitingApproval, rejected }

class PatientHomePageScreen extends StatefulWidget {
  final String? patientName;
  const PatientHomePageScreen({super.key, this.patientName});

  @override
  State<PatientHomePageScreen> createState() => _PatientHomePageScreenState();
}

class _PatientHomePageScreenState extends State<PatientHomePageScreen> {
  // Level 2 gets 3 places (locked selection, no search) — fewer choices than
  // Level 1's list, matching the recommendation that moderate-stage patients
  // need less to decide between, not more.
  final List<Map<String, dynamic>> recommendedPlaces = const [
    {'name': 'Market', 'lat': 13.7580, 'lng': 100.5040},
    {'name': 'Temple', 'lat': 13.7600, 'lng': 100.5100},
    {'name': 'Daughter\'s House', 'lat': 13.7650, 'lng': 100.5200},
  ];

  _ScreenState _state = _ScreenState.picking;
  Map<String, dynamic>? _selectedPlace;
  bool _sosSending = false;

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
    });

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        icon: const Icon(Icons.check_circle, color: Colors.green, size: 64),
        title: const Text('Alert Sent', style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
        content: const Text('Your caregiver has been notified.', style: TextStyle(fontSize: 18)),
        actions: [
          TextButton(
            onPressed: () {
              Navigator.of(context).pop();
              Navigator.push(
                context,
                MaterialPageRoute(builder: (context) => const SosContactScreen()),
              );
            },
            child: const Text('OK', style: TextStyle(fontSize: 18)),
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
