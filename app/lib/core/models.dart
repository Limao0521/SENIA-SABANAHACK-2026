import 'package:club_n_app/core/space.dart';
import 'package:club_n_app/core/role.dart';

enum DocType { cedulaCiudadania, cedulaExtranjeria, pasaporte, tarjetaIdentidad, otro }

extension DocTypeLabel on DocType {
  String get label => switch (this) {
    DocType.cedulaCiudadania => 'Cédula de ciudadanía',
    DocType.cedulaExtranjeria => 'Cédula de extranjería',
    DocType.pasaporte => 'Pasaporte',
    DocType.tarjetaIdentidad => 'Tarjeta de identidad',
    DocType.otro => 'Otro',
  };
}

class Guest {
  final String id;
  final DocType docType;
  final String docNumber;
  final String firstName;
  final String lastName;
  final String email;
  final DateTime registeredAt;

  Guest({
    String? id,
    required this.docType,
    required this.docNumber,
    required this.firstName,
    required this.lastName,
    required this.email,
    DateTime? registeredAt,
  })  : id = id ?? DateTime.now().microsecondsSinceEpoch.toString(),
        registeredAt = registeredAt ?? DateTime.now();
}

class Reservation {
  final String id;
  final ClubSpace space;
  final DateTime start;
  final DateTime end;
  final String holderName;
  final Role holderRole;

  Reservation({
    String? id,
    required this.space,
    required this.start,
    required this.end,
    required this.holderName,
    required this.holderRole,
  }) : id = id ?? DateTime.now().microsecondsSinceEpoch.toString();
}
