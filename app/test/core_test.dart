import 'package:flutter_test/flutter_test.dart';

import 'package:club_n_app/core/access.dart';
import 'package:club_n_app/core/auth.dart';
import 'package:club_n_app/core/email_service.dart';
import 'package:club_n_app/core/models.dart';
import 'package:club_n_app/core/repositories.dart';
import 'package:club_n_app/core/role.dart';
import 'package:club_n_app/core/space.dart';

void main() {
  group('matriz de accesos', () {
    test('regular no puede acceder a habitaciones', () {
      expect(canAccess(AccessRole.regular, ClubSpace.habitaciones), false);
    });

    test('vip puede acceder a habitaciones', () {
      expect(canAccess(AccessRole.vip, ClubSpace.habitaciones), true);
    });

    test('guest no puede acceder a bares', () {
      expect(canAccess(AccessRole.guest, ClubSpace.bares), false);
    });

    test('guest puede acceder a biblioteca', () {
      expect(canAccess(AccessRole.guest, ClubSpace.biblioteca), true);
    });

    test('staff puede acceder a oficinas', () {
      expect(canAccess(AccessRole.staff, ClubSpace.oficinas), true);
    });

    test('staff no puede acceder a bares', () {
      expect(canAccess(AccessRole.staff, ClubSpace.bares), false);
    });

    test('logistics puede acceder a almacenes', () {
      expect(canAccess(AccessRole.logistics, ClubSpace.almacenes), true);
    });

    test('maintenance puede acceder a gimnasio', () {
      expect(canAccess(AccessRole.maintenance, ClubSpace.gimnasio), true);
    });

    test('kAccessMatrix tiene 15 entradas', () {
      expect(kAccessMatrix.length, 15);
    });
  });

  group('reservas por rol', () {
    test('regular incluye restaurantes y bares', () {
      final spaces = reservableSpaces(Role.regular);
      expect(spaces.contains(ClubSpace.restaurantes), true);
      expect(spaces.contains(ClubSpace.bares), true);
    });

    test('regular no incluye habitaciones, oficinas, almacenes, cuartosTecnicos', () {
      final spaces = reservableSpaces(Role.regular);
      expect(spaces.contains(ClubSpace.habitaciones), false);
      expect(spaces.contains(ClubSpace.oficinas), false);
      expect(spaces.contains(ClubSpace.almacenes), false);
      expect(spaces.contains(ClubSpace.cuartosTecnicos), false);
    });

    test('vip no incluye oficinas, almacenes, cuartosTecnicos', () {
      final spaces = reservableSpaces(Role.vip);
      expect(spaces.contains(ClubSpace.oficinas), false);
      expect(spaces.contains(ClubSpace.almacenes), false);
      expect(spaces.contains(ClubSpace.cuartosTecnicos), false);
    });

    test('vip incluye habitaciones', () {
      final spaces = reservableSpaces(Role.vip);
      expect(spaces.contains(ClubSpace.habitaciones), true);
    });

    test('reservableSpaces(Role.regular).length == 11', () {
      expect(reservableSpaces(Role.regular).length, 11);
    });
  });

  group('solapes', () {
    test('detecta solape parcial', () {
      final repo = ReservationRepository();
      repo.add(Reservation(
        space: ClubSpace.salasJuntas,
        start: DateTime(2026, 1, 1, 10, 0),
        end: DateTime(2026, 1, 1, 12, 0),
        holderName: 'Test',
        holderRole: Role.vip,
      ));
      expect(
        repo.hasOverlap(
          ClubSpace.salasJuntas,
          DateTime(2026, 1, 1, 11, 0),
          DateTime(2026, 1, 1, 13, 0),
        ),
        true,
      );
    });

    test('detecta contención', () {
      final repo = ReservationRepository();
      repo.add(Reservation(
        space: ClubSpace.salasJuntas,
        start: DateTime(2026, 1, 1, 10, 0),
        end: DateTime(2026, 1, 1, 12, 0),
        holderName: 'Test',
        holderRole: Role.vip,
      ));
      expect(
        repo.hasOverlap(
          ClubSpace.salasJuntas,
          DateTime(2026, 1, 1, 11, 0),
          DateTime(2026, 1, 1, 11, 30),
        ),
        true,
      );
    });

    test('no solape contiguo (end == start de otra no solapa)', () {
      final repo = ReservationRepository();
      repo.add(Reservation(
        space: ClubSpace.salasJuntas,
        start: DateTime(2026, 1, 1, 10, 0),
        end: DateTime(2026, 1, 1, 12, 0),
        holderName: 'Test',
        holderRole: Role.vip,
      ));
      expect(
        repo.hasOverlap(
          ClubSpace.salasJuntas,
          DateTime(2026, 1, 1, 12, 0),
          DateTime(2026, 1, 1, 14, 0),
        ),
        false,
      );
    });
  });

  group('emails demo', () {
    test('DemoEmailService agrega SentEmail con email y qrAssetPath correctos', () async {
      final service = DemoEmailService();
      final guest = Guest(
        docType: DocType.cedulaCiudadania,
        docNumber: '123456789',
        firstName: 'Juan',
        lastName: 'Pérez',
        email: 'juan@example.com',
      );
      await service.sendGuestCredential(guest);
      expect(service.outbox.length, 1);
      expect(service.outbox.first.to, 'juan@example.com');
      expect(service.outbox.first.qrAssetPath, 'assets/qr/qr_guest.png');
    });
  });

  group('auth', () {
    final authService = AuthService();

    test('authenticate vip correcto devuelve rol vip', () {
      final user = authService.authenticate('vip@clubn.co', 'vip123');
      expect(user, isNotNull);
      expect(user!.role, Role.vip);
    });

    test('authenticate normaliza mayúsculas y espacios (staff)', () {
      final user = authService.authenticate('STAFF@clubn.co ', 'staff123');
      expect(user, isNotNull);
      expect(user!.role, Role.staff);
    });

    test('authenticate con password incorrecto devuelve null', () {
      final user = authService.authenticate('vip@clubn.co', 'mal');
      expect(user, isNull);
    });
  });
}
