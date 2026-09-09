import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import '../theme/app_theme.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart' as gmaps;

/// The patient drawn as their own face in a circle, not a map pin.
///
/// A caregiver looking at this map is looking for a person, and every screen
/// that shows them should show the same thing — the tracking map and the
/// navigation map disagreeing about what a patient looks like is a small
/// thing that costs a moment of "which dot is which" at the worst time.
///
/// Falls back to a person glyph on blue when there is no photo, which is the
/// normal case: profile images are held on the caregiver's device and never
/// sent to the backend, so they do not survive a reinstall.
Future<gmaps.BitmapDescriptor> buildPatientMarkerIcon(File? profileImage) async {
  const double size = 64;
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder, const Rect.fromLTWH(0, 0, size, size));
  const center = Offset(size / 2, size / 2);
  const radius = size / 2;

  var drewPhoto = false;
  if (profileImage != null) {
    try {
      final bytes = await profileImage.readAsBytes();
      final codec = await ui.instantiateImageCodec(
        bytes,
        targetWidth: size.toInt(),
        targetHeight: size.toInt(),
      );
      final frame = await codec.getNextFrame();
      canvas.save();
      canvas.clipPath(ui.Path()..addOval(Rect.fromCircle(center: center, radius: radius - 4)));
      paintImage(
        canvas: canvas,
        rect: Rect.fromCircle(center: center, radius: radius - 4),
        image: frame.image,
        fit: BoxFit.cover,
      );
      canvas.restore();
      drewPhoto = true;
    } catch (_) {
      drewPhoto = false;
    }
  }

  if (!drewPhoto) {
    canvas.drawCircle(center, radius - 4, Paint()..color = AppColors.primary);
    final iconPainter = TextPainter(textDirection: TextDirection.ltr)
      ..text = TextSpan(
        text: String.fromCharCode(Icons.person.codePoint),
        style: TextStyle(
          fontSize: radius,
          fontFamily: Icons.person.fontFamily,
          package: Icons.person.fontPackage,
          color: Colors.white,
        ),
      )
      ..layout();
    iconPainter.paint(
      canvas,
      center - Offset(iconPainter.width / 2, iconPainter.height / 2),
    );
  }

  canvas.drawCircle(
    center,
    radius - 0.5,
    Paint()
      ..color = Colors.white
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1,
  );

  final picture = recorder.endRecording();
  final image = await picture.toImage(size.toInt(), size.toInt());
  final byteData = await image.toByteData(format: ui.ImageByteFormat.png);
  // Without an explicit logical size, the platform draws the PNG's raw pixels
  // 1:1 against device pixels — on a high-DPI phone that makes a 64x64 bitmap
  // render far larger on-screen than a 64dp widget would.
  return gmaps.BitmapDescriptor.bytes(
    byteData!.buffer.asUint8List(),
    width: 36,
    height: 36,
  );
}
