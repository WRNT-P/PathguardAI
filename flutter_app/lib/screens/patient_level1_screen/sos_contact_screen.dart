import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../services/sos_service.dart';

Future<void> _callNumber(BuildContext context, String phone) async {
  final uri = Uri(scheme: 'tel', path: phone);
  if (!await launchUrl(uri)) {
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Could not call $phone')),
    );
  }
}

class CaregiverCard extends StatelessWidget {
  final String name;
  final bool busy;
  final String phone;

  const CaregiverCard({
    super.key,
    required this.name,
    required this.busy,
    required this.phone,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.grey[100],
        borderRadius: BorderRadius.circular(25),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Align(
            alignment: Alignment.topRight,
            child: Text(
              busy ? 'ไม่ว่าง' : 'ว่าง',
              style: TextStyle(
                fontSize: 12,
                color: busy ? Colors.red : Colors.green,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          const SizedBox(height: 8),

          Center(
            child: Icon(Icons.person, size: 40),
          ),
          const SizedBox(height: 4),

          Center(
            child: Text(name, style: TextStyle(fontWeight: FontWeight.bold)),
          ),
          const SizedBox(height: 2),

          Center(
            child: ElevatedButton.icon(
              onPressed: () => _callNumber(context, phone),
              icon: const Icon(Icons.phone, size: 16),
              label: const Text('Phone'),
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.green,
                foregroundColor: Colors.white,
                minimumSize: const Size(0, 32),
                padding: const EdgeInsets.symmetric(horizontal: 12),
              ),
            )
          )
        ],
      ),
    );
  }
}

class SosContactsScreen extends StatefulWidget{
  const SosContactsScreen({super.key});

  @override
  State<SosContactsScreen> createState() => _SosContactsScreenState();
}

class _SosContactsScreenState extends State<SosContactsScreen> {
  final List<Map<String, dynamic>> caregivers = const [
    {'name': 'คุณป้ามานี', 'busy': false, 'phone': '0812345678'},
    {'name': 'คุณลุงแดง', 'busy': false, 'phone': '0898765432'},
    {'name': 'พี่เขียว', 'busy': true, 'phone': '0865551234'},
    {'name': 'น้องส้ม', 'busy': true, 'phone': '0623334455'},
  ];

  bool _sosSending = false;

  Future<void> _handleSOS() async{
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
            SingleChildScrollView(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
              child: Column(
                children: [
                  const SizedBox(height: 40),
                  _buildSosButton(),
                  const SizedBox(height: 32),
                  const Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      'Contact your caregiver',
                      style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                    ),
                  ),
                  const SizedBox(height: 12),
                  GridView.count(
                    crossAxisCount: 2,
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    mainAxisSpacing: 12,
                    crossAxisSpacing: 12,
                    childAspectRatio: 0.85,
                    children: caregivers.map((caregiver) {
                      return CaregiverCard(
                        name: caregiver['name'],
                        busy: caregiver['busy'],
                        phone: caregiver['phone'],
                      );
                    }).toList(),
                  ),
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