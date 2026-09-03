import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../services/sos_service.dart';
import '../../services/api_client.dart';
import '../../services/session.dart';

Future<void> _callNumber(BuildContext context, String phone) async {
  final uri = Uri(scheme: 'tel', path: phone);
  if (!await launchUrl(uri)) {
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Could not call $phone')),
    );
  }
}

class CaregiverTile extends StatelessWidget {
  final String name;
  final String? phone;
  final bool? isAvailable;

  const CaregiverTile({
    super.key,
    required this.name,
    required this.phone,
    required this.isAvailable,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      height: 110,
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16),
      decoration: BoxDecoration(
        color: Colors.grey[200],
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          const CircleAvatar(
            radius: 28,
            backgroundColor: Colors.blue,
            child: Icon(Icons.person, size: 32, color: Colors.white),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  name,
                  style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
                ),
                // null = never toggled a status — must not read as "Unavailable".
                Text(
                  isAvailable == null ? 'Unknown status' : (isAvailable! ? 'Available' : 'Unavailable'),
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: isAvailable == null
                        ? Colors.black45
                        : (isAvailable! ? Colors.green : Colors.red),
                  ),
                ),
              ],
            ),
          ),
          ElevatedButton.icon(
            onPressed: phone == null ? null : () => _callNumber(context, phone!),
            icon: const Icon(Icons.phone, size: 24),
            label: const Text('Call', style: TextStyle(fontSize: 18)),
            style: ElevatedButton.styleFrom(
              backgroundColor: Colors.green,
              foregroundColor: Colors.white,
              minimumSize: const Size(20, 56),
            )
          )
        ],
      ),
    );
  }
}

class SosContactScreen extends StatefulWidget {
  const SosContactScreen({super.key});

  @override
  State<SosContactScreen> createState() => _SosContactScreenState();
 }

 class _SosContactScreenState extends State<SosContactScreen>{
  List<Map<String, dynamic>> caregivers = [];

  @override
  void initState() {
    super.initState();
    _loadCaregivers();
  }

  /// Backend already ranks by distance — see GET .../caregivers's own sort.
  Future<void> _loadCaregivers() async {
    final patientId = Session.instance.patientId;
    if (patientId == null) return;
    try {
      final res = await apiGet('/api/patients/$patientId/caregivers');
      if (res.statusCode != 200) return;
      final loaded = (jsonDecode(res.body)['caregivers'] as List).cast<Map<String, dynamic>>();
      if (mounted) setState(() => caregivers = loaded);
    } catch (_) {
    }
  }

  bool _sosSending = false;

  Future<void> _handleSOS() async {
    setState(() {
      _sosSending = true;
    });

    await triggerSOS();

    if(!mounted) return;
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
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('OK', style: TextStyle(fontSize: 18)),
          ),
        ],
      ),
    );
  }

  Widget _buildSosButton(){
    return GestureDetector(
      onTap: _sosSending ? null : _handleSOS,
      child: SizedBox(
        width: 220,
        height: 220,
        child: Stack(
          alignment: Alignment.center,
          children: [
            Container(
              width: 220,
              height: 220,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: Colors.red.withOpacity(0.15),
              ),
            ),
            Container(
              width: 160,
              height: 160,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: Colors.red.withOpacity(0.35),
              ),
            ),
            Container(
              width: 110,
              height: 110,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: Colors.red,
              ),
              child: const Center (
                child: Text(
                  'SOS',
                  style: TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.bold),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
  
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Stack(
          children: [
            Padding(
              padding: const EdgeInsets.all(16.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const SizedBox(height: 70),
                  Center(child: _buildSosButton()),
                  const SizedBox(height: 32),
                  const Text(
                    'Your contacts',
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 16),
                  ...caregivers.map((c) => CaregiverTile(
                        name: c['name'] as String,
                        phone: c['phone'] as String?,
                        isAvailable: c['is_available'] as bool?,
                      )),
                ],
              ),
            ),
            Positioned(
              top: 16,
              left: 16,
              child: Container(
                decoration: BoxDecoration(
                  color: Colors.grey[200],
                  shape: BoxShape.circle,
                ),
                child: IconButton(
                  icon: const Icon(
                    Icons.arrow_back,
                    color: Colors.black,
                    size: 20,
                  ),
                  onPressed: () {
                    Navigator.pop(context);
                  },
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}