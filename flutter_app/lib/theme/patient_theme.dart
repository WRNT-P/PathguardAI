import 'package:flutter/material.dart';

/// Shared accent palette for the patient-facing screens (and the app-wide
/// theme in main.dart). These three are highlight/accent colors layered onto
/// the existing UI — headers, cards, secondary buttons, selected states —
/// not a replacement for the load-bearing safety colors used elsewhere
/// (red = SOS/danger, orange = warning, green = safe/success). Do not reuse
/// these three for anything safety-critical.
class PatientColors {
  PatientColors._();

  /// Deep magenta/berry — used for primary headers, primary buttons, and
  /// selected/emphasis states across patient screens.
  static const Color berry = Color(0xFF8E2F63);
  static const Color berryDark = Color(0xFF6E2350);
  static const Color berryLight = Color(0xFFF6E4EE);

  /// Soft lavender/periwinkle — used for calm secondary surfaces (cards,
  /// chips, non-urgent secondary buttons).
  static const Color lavender = Color(0xFFC9C4F0);
  static const Color lavenderDark = Color(0xFF8B82D6);
  static const Color lavenderLight = Color(0xFFF1EFFB);

  /// Charcoal gray — used for app bars/headers and primary body text, giving
  /// a calm, neutral, high-contrast backdrop.
  static const Color charcoal = Color(0xFF3A3A3A);
  static const Color charcoalLight = Color(0xFFEDEDED);

  /// Safety-critical colors, named here only so call sites can reference one
  /// place — the actual values are the standard Material red/orange/green
  /// already used everywhere and are NOT changed by this palette.
  static const Color danger = Colors.red;
  static const Color warning = Color(0xFFEF6C00); // Colors.orange.shade800
  static const Color safe = Color(0xFF2E7D32); // Colors.green.shade700

  /// A calm gradient (berry fading to white) for primary headers/hero areas.
  static LinearGradient berryHeaderGradient() => const LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [berry, Colors.white],
      );

  /// A calm gradient (lavender fading to white) for secondary/card surfaces.
  static LinearGradient lavenderCardGradient() => const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [lavenderLight, Colors.white],
      );
}

/// A round, high-contrast back button sized at the 48dp+ minimum touch
/// target this app aims for everywhere on the patient side. Centralised so
/// every patient screen's "back" affordance looks and behaves the same way
/// (consistent icon meaning across screens).
class PatientBackButton extends StatelessWidget {
  const PatientBackButton({super.key});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: 'Go back',
      child: Container(
        decoration: const BoxDecoration(
          color: Colors.white,
          shape: BoxShape.circle,
          boxShadow: [
            BoxShadow(color: Colors.black26, blurRadius: 4, offset: Offset(0, 1)),
          ],
        ),
        width: 52,
        height: 52,
        child: IconButton(
          icon: const Icon(Icons.arrow_back, color: PatientColors.charcoal, size: 26),
          tooltip: 'Go back',
          onPressed: () => Navigator.pop(context),
        ),
      ),
    );
  }
}
