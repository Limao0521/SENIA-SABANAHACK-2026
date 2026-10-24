import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:club_n_app/core/session.dart';
import 'package:club_n_app/core/repositories.dart';
import 'package:club_n_app/core/email_service.dart';
import 'ui/login_screen.dart';
import 'ui/home_shell.dart';

void main() {
  runApp(const ClubNApp());
}

class ClubNApp extends StatelessWidget {
  const ClubNApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => SessionController()),
        ChangeNotifierProvider(create: (_) => GuestRepository()),
        ChangeNotifierProvider(create: (_) => ReservationRepository()),
        ChangeNotifierProvider(create: (_) => createEmailService()),
      ],
      child: MaterialApp(
        title: 'Club N',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          useMaterial3: true,
          colorScheme: ColorScheme.fromSeed(
            seedColor: const Color(0xFF0E3B2E),
            brightness: Brightness.light,
          ),
          scaffoldBackgroundColor: const Color(0xFFF5F3EF),
          inputDecorationTheme: InputDecorationTheme(
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
            ),
            filled: true,
            fillColor: Colors.white,
          ),
          elevatedButtonTheme: ElevatedButtonThemeData(
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF0E3B2E),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12),
              ),
              padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 24),
            ),
          ),
        ),
        home: const Gate(),
      ),
    );
  }
}

class Gate extends StatelessWidget {
  const Gate({super.key});

  @override
  Widget build(BuildContext context) {
    final session = context.watch<SessionController>();
    if (session.user == null) {
      return const LoginPage();
    }
    return const HomeShell();
  }
}
