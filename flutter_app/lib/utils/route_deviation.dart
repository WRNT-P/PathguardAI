import 'package:latlong2/latlong.dart';

/// Distance in meters from [point] to the nearest vertex in [routePoints].
///
/// An approximation — nearest vertex, not the true nearest point on each
/// segment — but Directions API polylines for a walking route are dense
/// enough that the difference is a couple of metres, and this is cheap
/// enough to run on every GPS fix, unlike real point-to-segment distance.
double distanceToRoute(LatLng point, List<LatLng> routePoints) {
  if (routePoints.isEmpty) return 0;
  const distance = Distance();
  var nearest = double.infinity;
  for (final p in routePoints) {
    final d = distance.as(LengthUnit.Meter, point, p);
    if (d < nearest) nearest = d;
  }
  return nearest;
}
