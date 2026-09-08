import 'dart:io';
import 'package:flutter_dotenv/flutter_dotenv.dart';

/// The key for Google's **web service** APIs — Places and Directions, called
/// over HTTPS from Dart.
///
/// **This is not the key the map itself uses.** The `GoogleMap` widget is a
/// native view, and its key is `MAPS_API_KEY` in `android/local.properties`,
/// baked into AndroidManifest.xml by Gradle at build time. Nothing in Dart
/// ever reads it, and nothing here ever reaches the map.
///
/// Saying so out loud because the two used to be named as if they were the
/// same thing: the .env entry was `ANDROID_GOOGLE_MAPS_API_KEY`, which reads
/// exactly like the map's key and is not it. On 2026-09-08 the map went blank
/// because Google stopped authorizing the *other* key, and the obvious place
/// to look — the one with "MAPS" in its name — was the one file that had
/// nothing to do with it.
String? googleWebServicesKey() {
  final platform = Platform.isIOS ? 'IOS' : 'ANDROID';
  return dotenv.env['${platform}_GOOGLE_WEB_SERVICES_KEY']
      // Transitional. .env is gitignored, so this rename cannot reach a
      // teammate's machine through a pull — without the fallback their first
      // Places call would throw on a null key. Drop it once all three local
      // .env files carry the new name.
      ??
      dotenv.env['${platform}_GOOGLE_MAPS_API_KEY'];
}
