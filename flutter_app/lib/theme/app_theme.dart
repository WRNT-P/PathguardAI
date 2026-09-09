import 'package:flutter/material.dart';

/// The palette from the pitch deck (backend/docs/Screenshot 2026-09-09 *.png):
/// vivid violet on near-black, with lavender panels and a lot of white.
///
/// Brand colours only. Semantic colours — red for SOS and danger, green for
/// "available" and success, orange for warnings — deliberately stay Material's
/// own, because a dementia-care app must not make an emergency look like a
/// button.
class AppColors {
  AppColors._();

  /// Buttons, chips, links, the caregiver's own chat bubble.
  static const Color primary = Color(0xFF7B5CF5);

  /// Pressed / icon-badge shade of [primary].
  static const Color primaryDark = Color(0xFF5B3FD6);

  /// The deck's headline gradient runs off into this instead of pure black.
  static const Color ink = Color(0xFF0E0B14);

  /// Lavender panel — replaces every `Colors.blue[50]` tint.
  static const Color lavender = Color(0xFFEDE8FF);

  /// Lavender border / stronger tint — replaces `Colors.blue[200]`.
  static const Color lavenderDeep = Color(0xFFD9D0FF);

  /// Page background: white with a hint of the lavender, not Material grey.
  static const Color background = Color(0xFFF7F5FF);

  static const Color surface = Colors.white;

  /// Header gradient, same direction as the deck's title slide.
  static const LinearGradient headerGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF9B7BFF), primary, ink],
    stops: [0.0, 0.45, 1.0],
  );
}

class AppTheme {
  AppTheme._();

  static ThemeData get light {
    final scheme = ColorScheme.fromSeed(
      seedColor: AppColors.primary,
      primary: AppColors.primary,
      onPrimary: Colors.white,
      surface: AppColors.surface,
      onSurface: AppColors.ink,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: AppColors.background,

      // The deck's headers are white type on the purple→black gradient. An
      // AppBar cannot take a gradient directly, so it takes the dark end and
      // the screens that own a custom header paint the gradient themselves.
      appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.ink,
        foregroundColor: Colors.white,
        elevation: 0,
        centerTitle: false,
        titleTextStyle: TextStyle(
          color: Colors.white,
          fontSize: 20,
          fontWeight: FontWeight.w700,
        ),
      ),

      // Pill buttons, bold labels — every slide's call-to-action is a pill.
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: AppColors.primary,
          foregroundColor: Colors.white,
          minimumSize: const Size(0, 48),
          shape: const StadiumBorder(),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
          elevation: 0,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.primaryDark,
          side: const BorderSide(color: AppColors.lavenderDeep, width: 1.5),
          minimumSize: const Size(0, 48),
          shape: const StadiumBorder(),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: AppColors.primaryDark,
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      floatingActionButtonTheme: const FloatingActionButtonThemeData(
        backgroundColor: AppColors.primary,
        foregroundColor: Colors.white,
        shape: StadiumBorder(),
      ),

      cardTheme: CardThemeData(
        color: AppColors.surface,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(24),
          side: const BorderSide(color: AppColors.lavenderDeep),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppColors.surface,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: AppColors.lavenderDeep),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: AppColors.lavenderDeep),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: AppColors.primary, width: 2),
        ),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: AppColors.lavender,
        labelStyle: const TextStyle(
          color: AppColors.primaryDark,
          fontWeight: FontWeight.w700,
        ),
        shape: const StadiumBorder(),
        side: BorderSide.none,
      ),
      progressIndicatorTheme:
          const ProgressIndicatorThemeData(color: AppColors.primary),
      snackBarTheme: const SnackBarThemeData(
        backgroundColor: AppColors.ink,
        contentTextStyle: TextStyle(color: Colors.white),
        behavior: SnackBarBehavior.floating,
      ),
      dividerColor: AppColors.lavenderDeep,
    );
  }
}
