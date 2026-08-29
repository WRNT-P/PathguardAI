import 'package:flutter/material.dart';
import 'dart:io';
import 'add_patient_screen.dart';
import 'track_screen.dart';
import 'notification_screen.dart';
import '../../services/patient_directory.dart';

class CaregiverHomePageScreen extends StatefulWidget {
  final String? caregiverName;
  const CaregiverHomePageScreen({super.key, this.caregiverName});

  @override
  State<CaregiverHomePageScreen> createState() => _CaregiverHomePageScreenState();
}


class _CaregiverHomePageScreenState extends State<CaregiverHomePageScreen> {
  List<Map<String, dynamic>> patients = [];

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.person_add_outlined, size: 56, color: Colors.grey[700]),
          const SizedBox(height: 12),
          FractionallySizedBox(
            widthFactor: 0.7,
            child: Text(
              'No patient added yet.',
              textAlign: TextAlign.center,
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w400,
                color: Colors.black87,
              ),
            ),
          ),
          const SizedBox(height: 12),
          ElevatedButton(
            onPressed: () async {
              final result = await Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (context) => const AddPatientScreen(),
                ),
              );
              if (result != null) {
                final id = PatientDirectory.instance.addPatient(result);
                setState(() {
                  patients.add({...result, 'id': id});
                });
                if (mounted) {
                  showDialog(
                    context: context,
                    builder: (context) => AlertDialog(
                      title: const Text('Patient Added'),
                      content: Text(
                        'Give this ID to the patient to log in:\n\n$id',
                        style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                      ),
                      actions: [
                        TextButton(
                          onPressed: () => Navigator.pop(context),
                          child: const Text('OK'),
                        ),
                      ],
                    ),
                  );
                }
              }
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: Colors.blue,
              minimumSize: const Size(0, 48),
            ),
            child: const Text(
              'Add Patient',
              style: TextStyle(color: Colors.white, fontSize: 16,),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPatientRow(Map<String, dynamic> patient) {
    final profileImage = patient['profileImage'] as File?;
    return InkWell(
      onTap: () {

      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Expanded(
              child: Row(
                children: [
                  CircleAvatar(
                    radius: 30, // Adjust size
                    backgroundColor: Colors.grey[300],
                    backgroundImage: profileImage != null ? FileImage(profileImage) : null,
                    child: profileImage == null ? Icon(Icons.person,size: 35, color: Colors.grey[800]): null,
                  ),
                  const SizedBox(width: 12),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(patient['name'],
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                        color: Colors.black87,
                      ),
                      ),
                      Text('ID: ${patient['id']}',
                      style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w700,
                        color: Colors.black87,
                      ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.blue,
                foregroundColor: Colors.white,
                elevation: 3,
                shadowColor: Colors.black,
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
              ),
              onPressed: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (context) => TrackScreen(patient: patient)
                  )
                );
              },
              child: const Text('Track'),
            ),
          ]
        ),
      ),
    );
  }

  
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            Container(
              color: Colors.grey[300],
              padding: const EdgeInsets.only(left: 20.0, right: 20.0, top: 12.0, bottom: 12.0),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children:
                    [
                      Text(
                        'Welcome Back,',
                        style: TextStyle(fontSize: 16, color: Colors.black87),
                      ),
                      SizedBox(height: 4),
                      Text(
                        widget.caregiverName ?? 'Caregiver',
                        style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.black87),
                      ),
                    ],
                  ),
                  Row(
                    children: [
                      IconButton(
                        onPressed: () {
                          Navigator.push(context, MaterialPageRoute(builder: (context) => const NotificationScreen()));
                        },
                        icon: const Badge(
                          smallSize: 10,
                          backgroundColor: Colors.red,
                          child: Icon(Icons.notifications_none_outlined, size: 28),
                        ),
                      ),
                      IconButton(
                        onPressed: () {
                          Navigator.of(context).popUntil((route) => route.isFirst);
                        },
                        icon: const Icon(Icons.logout),
                      ),
                    ],
                  ),
                ],
              ),
            ),

            // 2. Main content area (scrollable)
            Expanded(
              child: patients.isEmpty
                  ? _buildEmptyState()
                  : SingleChildScrollView(
                      child: Column(
                        children: patients.map((p) => _buildPatientRow(p)).toList(),
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }
}