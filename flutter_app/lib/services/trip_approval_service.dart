import 'trip_request_directory.dart';

/// Requests caregiver approval before a Level 2 patient can travel to [place].
/// Creates a real pending request and waits for a caregiver to decide via
/// TripRequestDirectory — no more auto-approving after a fixed delay.
Future<bool> requestTripApproval({
  required String patientName,
  required Map<String, dynamic> place,
}) async {
  final request = await TripRequestDirectory.instance.create(
    patientName: patientName,
    place: place,
  );
  return request.decision;
}
