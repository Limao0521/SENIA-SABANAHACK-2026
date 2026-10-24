import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:club_n_app/core/session.dart';
import 'package:club_n_app/core/repositories.dart';
import 'package:club_n_app/core/email_service.dart';
import 'package:club_n_app/core/space.dart';
import 'package:club_n_app/ui/login_screen.dart';
import 'package:club_n_app/ui/home_shell.dart';

/// Replicates the MultiProvider setup from main.dart for tests.
Widget buildTestApp() {
  return MultiProvider(
    providers: [
      ChangeNotifierProvider(create: (_) => SessionController()),
      ChangeNotifierProvider(create: (_) => GuestRepository()),
      ChangeNotifierProvider(create: (_) => ReservationRepository()),
      ChangeNotifierProvider(create: (_) => createEmailService()),
    ],
    child: MaterialApp(
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF0E3B2E),
        ),
      ),
      home: _TestGate(),
    ),
  );
}

/// Mirrors the Gate widget in main.dart.
class _TestGate extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final session = context.watch<SessionController>();
    if (session.user == null) {
      return const LoginPage();
    }
    return const HomeShell();
  }
}

/// Logs in using credentials (username + password + tap button).
Future<void> loginWith(
  WidgetTester tester, {
  required String username,
  required String password,
}) async {
  // Fill username
  final usernameField = find.widgetWithText(TextField, 'Usuario');
  await tester.enterText(usernameField, username);

  // Fill password
  final passwordField = find.widgetWithText(TextField, 'Contraseña');
  await tester.enterText(passwordField, password);

  // Tap submit
  await tester.tap(find.text('Ingresar al club'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('Staff login: muestra carnet, no muestra tabs Invitados/Reservas',
      (tester) async {
    await tester.pumpWidget(buildTestApp());
    await tester.pumpAndSettle();

    await loginWith(
      tester,
      username: 'staff@clubn.co',
      password: 'staff123',
    );

    // Staff should see carnet page with the helper text
    expect(find.text('Presenta este código en recepción'), findsOneWidget);

    // Staff should NOT see bottom nav items
    expect(find.text('Invitados'), findsNothing);
    expect(find.text('Reservas'), findsNothing);
    expect(find.text('Accesos'), findsNothing);

    // AppBar should say 'Club N · Staff'
    expect(find.text('Club N · Staff'), findsOneWidget);
  });

  testWidgets('Regular login: bottom nav con Invitados y Reservas visibles',
      (tester) async {
    await tester.pumpWidget(buildTestApp());
    await tester.pumpAndSettle();

    await loginWith(
      tester,
      username: 'regular@clubn.co',
      password: 'regular123',
    );

    // Regular should see bottom nav with all 4 tabs
    expect(find.text('Invitados'), findsOneWidget);
    expect(find.text('Reservas'), findsOneWidget);
    expect(find.text('Inicio'), findsOneWidget);
    expect(find.text('Carnet'), findsOneWidget);

    // Landing tab is the club info page
    expect(find.text('Nuestra historia'), findsOneWidget);

    // Carnet reachable from its tab
    await tester.tap(find.text('Carnet'));
    await tester.pumpAndSettle();
    expect(find.text('Presenta este código en recepción'), findsOneWidget);
  });

  testWidgets(
      'Regular registra invitado: aparece diálogo Correo enviado y outbox correcto',
      (tester) async {
    await tester.pumpWidget(buildTestApp());
    await tester.pumpAndSettle();

    await loginWith(
      tester,
      username: 'regular@clubn.co',
      password: 'regular123',
    );

    // Tap Invitados tab
    await tester.tap(find.text('Invitados'));
    await tester.pumpAndSettle();

    // Fill form: select doc type (already defaults to cedulaCiudadania),
    // enter doc number
    final docNumberField =
        find.widgetWithText(TextFormField, 'Número de documento');
    await tester.enterText(docNumberField, '123456789');

    final firstNameField = find.widgetWithText(TextFormField, 'Nombres');
    await tester.enterText(firstNameField, 'Juan');

    final lastNameField = find.widgetWithText(TextFormField, 'Apellidos');
    await tester.enterText(lastNameField, 'Pérez Gómez');

    final emailField =
        find.widgetWithText(TextFormField, 'Correo electrónico');
    await tester.enterText(emailField, 'juan.perez@example.com');

    await tester.pump();

    // Scroll to the register button, then tap it (disambiguate from the section title)
    final registerButton =
        find.widgetWithText(ElevatedButton, 'Registrar invitado');
    await tester.ensureVisible(registerButton);
    await tester.pumpAndSettle();
    await tester.tap(registerButton);
    await tester.pumpAndSettle();

    // Dialog 'Correo enviado' should appear
    expect(find.text('Correo enviado'), findsOneWidget);

    // EmailService outbox should have 1 item
    final ctx = tester.element(find.byType(MaterialApp));
    final emailService =
        Provider.of<EmailService>(ctx, listen: false);
    expect(emailService.outbox.length, 1);
    expect(emailService.outbox.first.to, 'juan.perez@example.com');

    // Close dialog
    await tester.tap(find.text('Cerrar'));
    await tester.pumpAndSettle();
  });

  testWidgets('Credencial incorrecta muestra error y no navega',
      (tester) async {
    await tester.pumpWidget(buildTestApp());
    await tester.pumpAndSettle();

    // Fill bad credentials
    final usernameField = find.widgetWithText(TextField, 'Usuario');
    await tester.enterText(usernameField, 'wrong@clubn.co');

    final passwordField = find.widgetWithText(TextField, 'Contraseña');
    await tester.enterText(passwordField, 'wrongpass');

    await tester.tap(find.text('Ingresar al club'));
    await tester.pumpAndSettle();

    // Error message should appear
    expect(find.text('Usuario o contraseña incorrectos.'), findsOneWidget);

    // Should still be on login page (no HomeShell)
    expect(find.byType(HomeShell), findsNothing);
    expect(find.text('Iniciar sesión'), findsOneWidget);
  });

  testWidgets(
      'Regular: dropdown reservas NO muestra Habitaciones de alojamiento',
      (tester) async {
    await tester.pumpWidget(buildTestApp());
    await tester.pumpAndSettle();

    await loginWith(
      tester,
      username: 'regular@clubn.co',
      password: 'regular123',
    );

    // Tap Reservas tab
    await tester.tap(find.text('Reservas'));
    await tester.pumpAndSettle();

    // Open the dropdown by tapping it
    await tester.tap(find.byType(DropdownButtonFormField<ClubSpace>));
    await tester.pumpAndSettle();

    // 'Restaurantes y cafeterías' should appear
    expect(find.text('Restaurantes y cafeterías'), findsOneWidget);
    // 'Habitaciones de alojamiento' should NOT appear for regular
    expect(find.text('Habitaciones de alojamiento'), findsNothing);
  });

  testWidgets('VIP: dropdown reservas SÍ muestra Habitaciones de alojamiento',
      (tester) async {
    await tester.pumpWidget(buildTestApp());
    await tester.pumpAndSettle();

    await loginWith(
      tester,
      username: 'vip@clubn.co',
      password: 'vip123',
    );

    // Tap Reservas tab
    await tester.tap(find.text('Reservas'));
    await tester.pumpAndSettle();

    // Open the dropdown
    await tester.tap(find.byType(DropdownButtonFormField<ClubSpace>));
    await tester.pumpAndSettle();

    // 'Habitaciones de alojamiento' should appear for VIP
    expect(find.text('Habitaciones de alojamiento'), findsOneWidget);
  });
}
