import 'package:flutter/material.dart';
import 'dart:ui' as ui;
import 'package:latlong2/latlong.dart';
import 'package:geolocator/geolocator.dart';
import 'package:flutter_compass/flutter_compass.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;
import 'dart:async';
import '../../utils/bearing.dart';
import '../../services/sos_service.dart';
import '../../services/directions_service.dart';

class NavigationScreen extends StatefulWidget{
  final Map<String, dynamic> place;
  const NavigationScreen({super.key, required this.place});
  
  @override 
  State<NavigationScreen> createState() => _NavigationScreenState();

}

class _NavigationScreenState extends State<NavigationScreen>{
  StreamSubscription<Position>? _positionSubscription;
  StreamSubscription<CompassEvent>? _compassSubscription;
  double? _heading;
  LatLng? _currentLocation;
  List<LatLng>? _routePoints;
  List<RouteStep>? _routeSteps;
  gmaps.GoogleMapController? _mapController;
  gmaps.BitmapDescriptor? _navigationIcon;
  bool _sosSending = false;
  /// false (default) = the camera rotates to keep the path ahead pointing
  /// up, so the arrow always reads as "pointing forward" while the map turns
  /// underneath it — a chase camera, not the arrow itself turning. true =
  /// north-up: the map stays fixed with north at the top like a paper map,
  /// and the arrow rotates in place to show which way the path actually
  /// runs instead. Same toggle Google Maps' own compass button switches
  /// between.
  bool _northUp = false;
  /// True once the magnetometer has actually produced a reading. Not every
  /// device has one — an emulator never does, and some budget handsets don't
  /// either — and until this flips we steer by the direction of travel
  /// instead. Without it the screen sat on a spinner forever, because
  /// ``_heading`` had exactly one writer and that writer never fired.
  bool _compassHasReported = false;
  // When the compass last caused a repaint, and the heading that was drawn —
  // see _startCompassUpdates for why the filter and the repaint are throttled
  // separately.
  DateTime? _lastCompassPaint;
  double? _paintedHeading;
  /// Location was refused (or the service is off). Kept so the screen can say
  /// so: the old code just returned out of ``_startLocationUpdates`` and left
  /// the same spinner up, which is indistinguishable from "still loading" and
  /// tells a patient nothing they can act on.
  bool _locationUnavailable = false;

  Future<bool> _ensureLocationPermission() async {
    bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
    
    if (!serviceEnabled) return false;
    LocationPermission permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    return permission == LocationPermission.whileInUse ||
        permission == LocationPermission.always;
  }

  @override
  void initState() {
    super.initState();
    _startLocationUpdates();
    _startCompassUpdates();
    _loadNavigationIcon();
    _routeSteps = [];

  }

  /// Draws Material's navigation-arrow glyph to a bitmap once, so it can be
  /// used as the patient's marker icon on the map instead of a giant Icon
  /// floating in screen-space — a real marker is pinned to a lat/lng, sized
  /// like every other pin on the map, and rides along with the tilt/rotation
  /// the camera already applies, instead of sitting fixed over the middle of
  /// the screen looking three sizes too big for anything around it.
  Future<void> _loadNavigationIcon() async {
    const double size = 96;

    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder, const Rect.fromLTWH(0, 0, size, size));
    const center = Offset(size / 2, size / 2);

    final painter = TextPainter(textDirection: TextDirection.ltr)
      ..text = TextSpan(
        text: String.fromCharCode(Icons.navigation.codePoint),
        style: TextStyle(
          fontSize: size * 0.75,
          fontFamily: Icons.navigation.fontFamily,
          package: Icons.navigation.fontPackage,
          color: Colors.blue,
        ),
      )
      ..layout();

    painter.paint(canvas, center - Offset(painter.width / 2, painter.height / 2));

    final picture = recorder.endRecording();
    final image = await picture.toImage(size.toInt(), size.toInt());
    final byteData = await image.toByteData(format: ui.ImageByteFormat.png);

    final icon = gmaps.BitmapDescriptor.bytes(
      byteData!.buffer.asUint8List(),
      width: 48,
      height: 48,
    );

    if (mounted) setState(() => _navigationIcon = icon);
  }

  Future<void> _startLocationUpdates() async {
    final hasPermission = await _ensureLocationPermission();
    if (!hasPermission) {
      if (mounted) setState(() => _locationUnavailable = true);
      return;
    }

    // getPositionStream alone can sit quiet for a long time before its first
    // event — it is a continuous-tracking stream, not a "give me a fix now"
    // call, and on a device (or emulator) slow to lock GPS that left this
    // screen on "Finding your location…" far longer than the level 1 screen,
    // which fetches this same one-off fix before ever touching the stream.
    // Cached first: it returns at once and gets the map on screen, where the
    // fresh fix below can take the full five seconds. Level 2's screen is the
    // one that must not sit blank — this patient is meant to glance and go.
    try {
      final cached = await Geolocator.getLastKnownPosition();
      if (cached != null) _handlePosition(cached);
    } catch (_) {}

    try {
      final seed = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      ).timeout(const Duration(seconds: 5));
      _handlePosition(seed);
    } catch (_) {}

    _positionSubscription = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 5, // meters — only fire when moved at least this far
      ),
    ).listen(_handlePosition);
  }

  void _handlePosition(Position position) {
    final updated = LatLng(position.latitude, position.longitude);
    final isFirstFix = _currentLocation == null;
    final previous = _currentLocation;

    setState(() {
      _currentLocation = updated;
      // Only when there is no compass: the magnetometer knows which way the
      // patient is FACING, which is the right question here, while this only
      // knows which way they last MOVED. They agree while walking forward
      // and disagree when someone stops and turns on the spot — so the
      // compass wins whenever it exists, and this keeps the screen usable
      // when it doesn't.
      if (!_compassHasReported && previous != null) {
        _updateHeading(calculateBearing(
          previous.latitude, previous.longitude,
          updated.latitude, updated.longitude,
        ));
      }
    });

    _updateCamera();

    if (isFirstFix) {
      _fetchRoute();
    }
  }

  /// Re-centres the camera on the current position with whichever bearing
  /// [_northUp] calls for. Its own method (not inlined in _handlePosition)
  /// because toggling the compass button needs the exact same camera move
  /// without waiting for the next GPS fix to trigger it.
  void _updateCamera() {
    final current = _currentLocation;
    if (current == null) return;
    // newLatLngZoom can't carry tilt/bearing — newCameraPosition is the one
    // that keeps the 3D perspective on every move instead of snapping back
    // to flat/north-up.
    _mapController?.animateCamera(
      gmaps.CameraUpdate.newCameraPosition(
        gmaps.CameraPosition(
          target: gmaps.LatLng(current.latitude, current.longitude),
          zoom: 18.5,
          tilt: 60,
          bearing: _northUp ? 0 : (_bearingToTarget() ?? 0),
        ),
      ),
    );
  }

  void _toggleNorthUp() {
    setState(() => _northUp = !_northUp);
    _updateCamera();
  }

  /// Ease [rawHeading] into ``_heading`` instead of snapping to it.
  ///
  /// Shared by both sources on purpose. A GPS-derived bearing off 5 m steps is
  /// noisy enough that an unsmoothed arrow visibly jitters, and a moderate-stage
  /// patient reading a twitching arrow is being given a worse instruction than
  /// no arrow at all. Call inside a ``setState``.
  void _updateHeading(double rawHeading) {
    if (_heading == null) {
      _heading = rawHeading; // first reading — nothing to smooth against yet
      return;
    }
    const smoothingFactor = 0.15; // lower = smoother but slower to respond
    final delta = shortestAngleDelta(_heading!, rawHeading);
    _heading = (_heading! + delta * smoothingFactor) % 360;
    if (_heading! < 0) _heading = _heading! + 360;
  }

  IconData _instructionIcon(String instruction) {
    switch (instruction) {
      case 'Turn left':
        return Icons.turn_left;
      case 'Turn right':
        return Icons.turn_right;
      case 'Turn around':
        return Icons.u_turn_left;
      case 'Go through the roundabout':
        return Icons.roundabout_left;
      case 'Arriving at destination':
        return Icons.flag;
      default:
        return Icons.straight;
    }
  }

  void _showDirectionsList() {
    showModalBottomSheet(
      context: context,
      builder: (context) => ListView.builder(
        itemCount: _routeSteps!.length,
        itemBuilder: (context, index) {
          final step = _routeSteps![index];
          return ListTile(
            leading: Icon(_instructionIcon(step.instruction)),
            title: Text(step.instruction),
            trailing: Text('${step.distanceMeters.toStringAsFixed(0)}m'),
          );
        },
      ),
    );
  }
    
  

  Future<void> _fetchRoute() async {
    final origin = gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude);
    final destination = gmaps.LatLng(widget.place['lat'], widget.place['lng']);
    final route = await fetchRoute(origin, destination);
    if (!mounted || route == null) return;
    setState(() {
      _routePoints = route.points.map((p) => LatLng(p.latitude, p.longitude)).toList();
      _routeSteps = route.steps;
    });
  }

  /// Finds the point [lookaheadMeters] ahead of [current] along [route] —
  /// this is what the arrow points at instead of the raw destination, so it
  /// follows the actual street shape instead of cutting through buildings.
  /// Index of the route point nearest the patient — how far along the line
  /// they are. Shared so the arrow and the drawn line can never disagree
  /// about where "here" is on the route.
  int _nearestRouteIndex(List<LatLng> route, LatLng current) {
    const distance = Distance();
    var nearestIndex = 0;
    var nearestDistance = double.infinity;
    for (var i = 0; i < route.length; i++) {
      final d = distance.as(LengthUnit.Meter, current, route[i]);
      if (d < nearestDistance) {
        nearestDistance = d;
        nearestIndex = i;
      }
    }
    return nearestIndex;
  }

  /// The part of the route still ahead, drawn from where the patient is now.
  ///
  /// The route is fetched once (Directions is billed per call), so its points
  /// never move — drawing them raw left the line starting wherever the walk
  /// began, however far along the patient actually was.
  List<gmaps.LatLng> _remainingRoute() {
    final route = _routePoints!;
    final here = _currentLocation;
    if (here == null) {
      return route.map((p) => gmaps.LatLng(p.latitude, p.longitude)).toList();
    }
    return [
      gmaps.LatLng(here.latitude, here.longitude),
      ...route
          .sublist(_nearestRouteIndex(route, here))
          .map((p) => gmaps.LatLng(p.latitude, p.longitude)),
    ];
  }

  LatLng _lookaheadTarget(List<LatLng> route, LatLng current, {double lookaheadMeters = 15}) {
    const distance = Distance();
    final nearestIndex = _nearestRouteIndex(route, current);

    var accumulated = 0.0;
    for (var i = nearestIndex; i < route.length - 1; i++) {
      accumulated += distance.as(LengthUnit.Meter, route[i], route[i + 1]);
      if (accumulated >= lookaheadMeters) {
        return route[i + 1];
      }
    }
    return route.last;
  }

  /// The compass bearing from here to just ahead along the route (or
  /// straight to the destination once there's no route yet) — this is the
  /// "forward" the arrow and the chase camera both follow. Deliberately not
  /// derived from ``_heading``: that's the direction the patient is actually
  /// facing/moving, which is a different question ("which way do I turn?")
  /// from "which way does the path go?", and conflating the two is what made
  /// the arrow and the camera rotate by two different amounts before.
  double? _bearingToTarget() {
    final current = _currentLocation;
    if (current == null) return null;
    final destination = LatLng(widget.place['lat'], widget.place['lng']);
    final target = (_routePoints != null && _routePoints!.length >= 2)
        ? _lookaheadTarget(_routePoints!, current)
        : destination;
    return calculateBearing(
      current.latitude, current.longitude,
      target.latitude, target.longitude,
    );
  }

  void _startCompassUpdates() {
    // ``FlutterCompass.events`` is null on a device with no magnetometer, and
    // on some that have one it is non-null but never emits. Neither case is an
    // error and neither used to be handled — the stream simply stayed quiet
    // and the screen waited on it forever.
    _compassSubscription = FlutterCompass.events?.listen((CompassEvent event) {
      final rawHeading = event.heading;
      if (rawHeading == null) return;

      // The smoothing filter still sees every sample. Throttling the samples
      // themselves would change how it behaves, not just how often it draws.
      final firstReport = !_compassHasReported;
      _compassHasReported = true;
      _updateHeading(rawHeading);

      // Painting is what gets throttled. A magnetometer reports tens of times
      // a second and each setState here rebuilds the GoogleMap along with
      // everything else — which is what put "Skipped 128 frames" in the log,
      // on a screen meant to stay open all day on a patient's phone. Ten
      // frames a second, and only once the arrow would visibly move.
      //
      // The first reading is exempt: it is what takes the screen out of its
      // "waiting for the compass" state, so it has to land immediately.
      final now = DateTime.now();
      final tooSoon = _lastCompassPaint != null &&
          now.difference(_lastCompassPaint!) < const Duration(milliseconds: 100);
      final tooSmall = _paintedHeading != null &&
          shortestAngleDelta(_paintedHeading!, _heading!).abs() < 1.0;
      if (!firstReport && (tooSoon || tooSmall)) return;

      _lastCompassPaint = now;
      _paintedHeading = _heading;
      if (mounted) setState(() {});
    });
  }
  

  @override
  void dispose() {
  _positionSubscription?.cancel();
  _compassSubscription?.cancel();
  super.dispose();
  }
  
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
          icon: const Icon(Icons.check_circle, color: Colors.green, size: 128),
          title: const Text('Alert Sent', style: TextStyle(
            fontSize: 24,
            fontWeight: FontWeight.bold
          )),
          content: const Text('Your caregiver has been notified.', style: TextStyle(fontSize: 18)),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('OK')
            )
          ],
        ),
      );
    }
  /// Turns a signed relative angle (-180..180, degrees to turn from the
  /// current heading to face the destination) into a short instruction.
  /// Kept coarse on purpose — a moderate-stage patient needs one unambiguous
  /// word, not a precise bearing.
  String _directionText(double relativeAngle) {
    final magnitude = relativeAngle.abs();
    if (magnitude < 20) return 'Go straight ahead';
    if (magnitude >= 150) return 'Turn around';
    return relativeAngle > 0 ? 'Turn right' : 'Turn left';
  }

  /// The centre graphic for states that aren't "walking with a known
  /// direction" — that state is now shown by the real navigation marker
  /// pinned to the map (see ``markers`` in build), not a screen-space icon,
  /// so it isn't handled here any more. Returns null (nothing to show, the
  /// map underneath speaks for itself) once a direction is known.
  Widget? _indicator(bool arrived) {
    if (arrived) {
      return const Icon(Icons.check_circle, color: Colors.green, size: 100);
    }
    if (_locationUnavailable) {
      return const Icon(Icons.location_off, color: Colors.orange, size: 100);
    }
    if (_currentLocation == null) {
      return const CircularProgressIndicator();
    }
    return null;
  }

  String _statusText(bool arrived, String? directionText) {
    if (arrived) return "You've arrived!";
    if (_locationUnavailable) return 'Turn on location to start';
    if (directionText != null) return directionText;
    if (_currentLocation != null) return 'Start walking to find your direction';
    return 'Finding your location…';
  }

  @override
  Widget build(BuildContext context) {
    final destination = LatLng(widget.place['lat'], widget.place['lng']);

    double? distanceInMeters;
    // Where the path ahead points — the arrow and the chase camera both
    // follow this, not _heading (which way the patient is physically
    // facing). Those used to be conflated, which is why the arrow and the
    // camera each rotated by a different amount instead of the arrow simply
    // always pointing "forward" on screen.
    final absoluteBearing = _bearingToTarget();
    String? directionText;

    if (_currentLocation != null) {
      distanceInMeters = const Distance().as(LengthUnit.Meter, _currentLocation!, destination);

      // The turn instruction still needs _heading — "which way do I turn"
      // is inherently relative to which way the patient is actually facing,
      // unlike the arrow/camera bearing above.
      if (_heading != null && absoluteBearing != null) {
        final relativeAngle = shortestAngleDelta(_heading!, absoluteBearing);
        directionText = _directionText(relativeAngle);
      }
    }

    final arrived = distanceInMeters != null && distanceInMeters < 20;

    // Always the path-ahead bearing, in both modes — north-up only changes
    // whether the *map* rotates to match it; the arrow itself always points
    // where the route goes.
    final markerRotation = absoluteBearing ?? 0;

    final markers = <gmaps.Marker>{
      gmaps.Marker(
        markerId: const gmaps.MarkerId('destination'),
        position: gmaps.LatLng(destination.latitude, destination.longitude),
        icon: gmaps.BitmapDescriptor.defaultMarkerWithHue(gmaps.BitmapDescriptor.hueRed),
      ),
      if (_currentLocation != null && _navigationIcon != null)
        gmaps.Marker(
          markerId: const gmaps.MarkerId('patient'),
          position: gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude),
          icon: _navigationIcon!,
          anchor: const Offset(0.5, 0.5),
          flat: true,
          rotation: markerRotation,
          zIndexInt: 1,
        )
      else if (_currentLocation != null)
        gmaps.Marker(
          markerId: const gmaps.MarkerId('patient'),
          position: gmaps.LatLng(_currentLocation!.latitude, _currentLocation!.longitude),
          icon: gmaps.BitmapDescriptor.defaultMarkerWithHue(gmaps.BitmapDescriptor.hueAzure),
        ),
    };

    final polylines = <gmaps.Polyline>{
      if (_routePoints != null && _routePoints!.length >= 2)
        gmaps.Polyline(
          polylineId: const gmaps.PolylineId('route'),
          points: _remainingRoute(),
          color: Colors.deepOrange,
          width: 4,
        ),
    };

    return Scaffold(
      appBar: AppBar(title: Text(widget.place['name']),
      actions: [
        IconButton(
          icon: const Icon(Icons.list),
          onPressed: _routeSteps == null || _routeSteps!.isEmpty
          ? null
          : _showDirectionsList,
        )
      ]
      ),
      // The map is a backdrop, not the interaction surface — this screen
      // still reads by its arrow and caption, same as before. It sits behind
      // them purely to give a moderate-stage patient's caregiver (looking
      // over their shoulder, or the patient themself) a sense of place
      // without turning the screen into something that has to be read.
      body: Stack(
        children: [
          Positioned.fill(
            child: gmaps.GoogleMap(
              initialCameraPosition: gmaps.CameraPosition(
                target: gmaps.LatLng(destination.latitude, destination.longitude),
                zoom: 25,
                tilt: 60,
              ),
              markers: markers,
              polylines: polylines,
              myLocationButtonEnabled: false,
              zoomControlsEnabled: false,
              // Pushes the camera's centre — where the patient marker sits —
              // down from the middle of the screen, so more of the map ahead
              // of them (not behind) is visible under the arrow/status
              // overlay. Lower than 0.55 (was covering the marker with the
              // status caption/SOS button at the bottom of the screen).
              padding: EdgeInsets.only(top: MediaQuery.of(context).size.height * 0.35),
              onMapCreated: (controller) => _mapController = controller,
            ),
          ),
          Column(
            children: [
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(24.0),
                color: Colors.white.withValues(alpha: 0.85),
                child: Text(
                  'Going to: ${widget.place['name']}',
                  style: const TextStyle(fontSize: 28, fontWeight: FontWeight.bold),
                  textAlign: TextAlign.center,
                ),
              ),
              Expanded(
                child: Builder(builder: (context) {
                  final indicator = _indicator(arrived);
                  // Nothing to show once a direction is known — the map's
                  // own navigation marker carries that now, and an empty
                  // white disc floating over the tilted map would just be a
                  // giant blank circle sitting on top of it.
                  if (indicator == null) return const SizedBox.shrink();
                  return Center(
                    child: Container(
                      width: 160,
                      height: 160,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: Colors.white.withValues(alpha: 0.85),
                      ),
                      child: Center(child: indicator),
                    ),
                  );
                }),
              ),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(16.0),
                color: Colors.white.withValues(alpha: 0.85),
                child: Text(
                  _statusText(arrived, directionText),
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w600),
                ),
              ),
              Padding(
                padding: const EdgeInsets.only(top: 16.0, bottom: 30.0),
                child: SizedBox(
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
                ),
              ),
            ],
          ),
          Positioned(
            top: 16,
            left: 16,
            child: SizedBox(
              width: 48,
              height: 48,
              child: FloatingActionButton(
                heroTag: 'northUpToggle',
                tooltip: _northUp ? 'Switch to direction-up' : 'Switch to north-up',
                backgroundColor: _northUp ? Colors.blue : Colors.white,
                onPressed: _toggleNorthUp,
                child: Icon(
                  Icons.explore,
                  color: _northUp ? Colors.white : Colors.blue,
                ),
              ),
            ),
          ),
          if (_routePoints != null && _routePoints!.length >= 2)
            Positioned(
              top: 16,
              right: 16,
              child: Card(
                elevation: 3,
                color: Colors.white.withValues(alpha: 0.9),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                child: SizedBox(
                  width: 140,
                  height: 120,
                  child: _RouteLinePreview(points: _routePoints!, currentLocation: _currentLocation),
                ),
              ),
            ),
        ],
      ),
    );
  }
}


/// A schematic route sketch — the shape of the walk from here to the
/// destination, not a real map. No tiles, no scale: just start (green),
/// finish (red pin), and a live dot for where the patient is right now, so a
/// glance says "this is the shape of where you're headed and how far along
/// you are" — kept alongside the real background map because the caregiver
/// asked to have both, not one instead of the other.
///
/// [points] is fetched once (see _fetchRoute) and deliberately never
/// refreshed as the patient walks — re-requesting a route from the
/// Directions API on every position update would be an unbounded, metered
/// call for a preview that doesn't need to be geometrically exact. Only
/// [currentLocation] moves in real time.
class _RouteLinePreview extends StatelessWidget {
  final List<LatLng> points;
  final LatLng? currentLocation;
  const _RouteLinePreview({required this.points, this.currentLocation});

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: _RoutePainter(points, currentLocation),
      child: Container(),
    );
  }
}

class _RoutePainter extends CustomPainter {
  final List<LatLng> points;
  final LatLng? currentLocation;
  static const double _padding = 16;

  _RoutePainter(this.points, this.currentLocation);

  @override
  void paint(Canvas canvas, Size size) {
    if (points.length < 2) return;

    var minLat = points.first.latitude, maxLat = points.first.latitude;
    var minLng = points.first.longitude, maxLng = points.first.longitude;
    for (final p in points) {
      if (p.latitude < minLat) minLat = p.latitude;
      if (p.latitude > maxLat) maxLat = p.latitude;
      if (p.longitude < minLng) minLng = p.longitude;
      if (p.longitude > maxLng) maxLng = p.longitude;
    }
    // The live dot can drift outside the original route's box (patient goes
    // off-route, or the route was fetched from a slightly different fix) —
    // widen the box to include it so it never gets clipped off the edge.
    final live = currentLocation;
    if (live != null) {
      if (live.latitude < minLat) minLat = live.latitude;
      if (live.latitude > maxLat) maxLat = live.latitude;
      if (live.longitude < minLng) minLng = live.longitude;
      if (live.longitude > maxLng) maxLng = live.longitude;
    }
    // A perfectly straight walk gives a zero-width bounding box on one axis —
    // fall back to a hairline span rather than divide by zero.
    final latSpan = (maxLat - minLat).abs() < 1e-9 ? 1e-9 : maxLat - minLat;
    final lngSpan = (maxLng - minLng).abs() < 1e-9 ? 1e-9 : maxLng - minLng;

    final availableW = size.width - _padding * 2;
    final availableH = size.height - _padding * 2;
    final scale = (availableW / lngSpan < availableH / latSpan)
        ? availableW / lngSpan
        : availableH / latSpan;

    // Centre the (possibly non-square) route shape in the box rather than
    // stretching it to fill — a straight path should look straight.
    final drawnW = lngSpan * scale;
    final drawnH = latSpan * scale;
    final offsetX = _padding + (availableW - drawnW) / 2;
    final offsetY = _padding + (availableH - drawnH) / 2;

    Offset project(LatLng p) => Offset(
          offsetX + (p.longitude - minLng) * scale,
          // Higher latitude is further north, which is up on screen —
          // opposite of how y grows downward in canvas coordinates.
          offsetY + (maxLat - p.latitude) * scale,
        );

    final path = ui.Path()..moveTo(project(points.first).dx, project(points.first).dy);
    for (final p in points.skip(1)) {
      final o = project(p);
      path.lineTo(o.dx, o.dy);
    }

    canvas.drawPath(
      path,
      Paint()
        ..color = Colors.deepOrange
        ..strokeWidth = 4
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round,
    );

    _drawStart(canvas, project(points.first));
    _drawFinish(canvas, project(points.last));
    if (live != null) _drawLive(canvas, project(live));
  }

  void _drawStart(Canvas canvas, Offset center) {
    canvas.drawCircle(center, 8, Paint()..color = Colors.white);
    canvas.drawCircle(center, 7, Paint()..color = Colors.green);
  }

  /// A classic map-pin teardrop, same silhouette as a default Google Maps
  /// marker, anchored by its point rather than its centre — the tip is what
  /// sits over the destination coordinate.
  void _drawFinish(Canvas canvas, Offset tip) {
    const pinHeight = 26.0, pinWidth = 20.0;
    final bulbCenter = Offset(tip.dx, tip.dy - pinHeight + pinWidth / 2);
    final radius = pinWidth / 2;

    final path = ui.Path()
      ..moveTo(tip.dx, tip.dy)
      ..lineTo(bulbCenter.dx - radius * 0.75, bulbCenter.dy + radius * 0.6)
      ..arcToPoint(
        Offset(bulbCenter.dx + radius * 0.75, bulbCenter.dy + radius * 0.6),
        radius: Radius.circular(radius),
        clockwise: false,
        largeArc: true,
      )
      ..close();

    canvas.drawPath(path, Paint()..color = Colors.black87);
    canvas.drawCircle(bulbCenter, radius, Paint()..color = Colors.red);
    canvas.drawCircle(bulbCenter, radius * 0.4, Paint()..color = Colors.white);
  }

  /// Where the patient is right now — the one thing on this preview that
  /// actually moves as they walk.
  void _drawLive(Canvas canvas, Offset center) {
    canvas.drawCircle(center, 9, Paint()..color = Colors.blue.withValues(alpha: 0.25));
    canvas.drawCircle(center, 6, Paint()..color = Colors.white);
    canvas.drawCircle(center, 5, Paint()..color = Colors.blue);
  }

  @override
  bool shouldRepaint(covariant _RoutePainter oldDelegate) =>
      oldDelegate.points != points || oldDelegate.currentLocation != currentLocation;
}
