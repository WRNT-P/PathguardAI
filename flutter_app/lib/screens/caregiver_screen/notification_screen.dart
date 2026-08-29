import 'package:flutter/material.dart';
import '../../services/trip_request_directory.dart';

class NotificationScreen extends StatefulWidget {
  const NotificationScreen({super.key});

  @override
  State<NotificationScreen> createState() => _NotificationScreenState();
}

class _NotificationScreenState extends State<NotificationScreen> {
  @override
  void initState() {
    super.initState();
    TripRequestDirectory.instance.addListener(_onRequestsChanged);
  }

  @override
  void dispose() {
    TripRequestDirectory.instance.removeListener(_onRequestsChanged);
    super.dispose();
  }

  void _onRequestsChanged() {
    setState(() {});
  }

  Widget _buildTripRequestTile(TripRequest request) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${request.patientName} want to ask to go to ${request.place['name']}',
              style: const TextStyle(fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: () => TripRequestDirectory.instance.decide(request.id, true),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.green,
                      foregroundColor: Colors.white,
                    ),
                    child: const Text('Approve'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: ElevatedButton(
                    onPressed: () => TripRequestDirectory.instance.decide(request.id, false),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.red,
                      foregroundColor: Colors.white,
                    ),
                    child: const Text('Reject'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final pending = TripRequestDirectory.instance.pending;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Notifications'),
      ),
      body: pending.isEmpty
          ? const Center(child: Text('No notifications'))
          : ListView(
              children: pending.map(_buildTripRequestTile).toList(),
            ),
    );
  }
}
