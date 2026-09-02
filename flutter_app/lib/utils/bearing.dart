import 'dart:math';

double calculateBearing(double fromLat, double fromLng, double toLat, double toLng) {
  final lat1 = fromLat * pi / 180;
  final lat2 = toLat * pi / 180;
  final dLng = (toLng - fromLng) * pi / 180; final y = sin(dLng) * cos(lat2);
  final x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dLng);

  final bearingRad = atan2(y, x);
  return (bearingRad * 180 / pi + 360) % 360;
}

double shortestAngleDelta(double from, double to) {
  double delta = (to - from) % 360;
  if (delta > 180) delta -= 360;
  if (delta < -180) delta += 360;
  return delta;
}