import 'package:club_n_app/core/role.dart';
import 'package:club_n_app/core/space.dart';

enum AccessRole { vip, regular, guest, staff, logistics, maintenance }

const Map<ClubSpace, Set<AccessRole>> kAccessMatrix = {
  ClubSpace.recepcion: {
    AccessRole.vip,
    AccessRole.regular,
    AccessRole.guest,
    AccessRole.staff,
    AccessRole.logistics,
    AccessRole.maintenance,
  },
  ClubSpace.restaurantes: {
    AccessRole.vip,
    AccessRole.regular,
    AccessRole.guest,
    AccessRole.staff,
  },
  ClubSpace.bares: {AccessRole.vip, AccessRole.regular},
  ClubSpace.convenciones: {
    AccessRole.vip,
    AccessRole.regular,
    AccessRole.guest,
    AccessRole.staff,
    AccessRole.logistics,
    AccessRole.maintenance,
  },
  ClubSpace.salasJuntas: {
    AccessRole.vip,
    AccessRole.regular,
    AccessRole.staff,
    AccessRole.maintenance,
  },
  ClubSpace.gastroworking: {AccessRole.vip, AccessRole.regular, AccessRole.staff},
  ClubSpace.gimnasio: {AccessRole.vip, AccessRole.regular, AccessRole.maintenance},
  ClubSpace.piscina: {AccessRole.vip, AccessRole.regular, AccessRole.maintenance},
  ClubSpace.biblioteca: {
    AccessRole.vip,
    AccessRole.regular,
    AccessRole.guest,
    AccessRole.staff,
  },
  ClubSpace.zonaInfantil: {
    AccessRole.vip,
    AccessRole.regular,
    AccessRole.staff,
    AccessRole.maintenance,
  },
  ClubSpace.habitaciones: {AccessRole.vip, AccessRole.staff, AccessRole.maintenance},
  ClubSpace.oficinas: {AccessRole.staff, AccessRole.maintenance},
  ClubSpace.almacenes: {AccessRole.staff, AccessRole.logistics, AccessRole.maintenance},
  ClubSpace.cuartosTecnicos: {AccessRole.staff, AccessRole.maintenance},
  ClubSpace.estacionamientos: {
    AccessRole.vip,
    AccessRole.regular,
    AccessRole.guest,
    AccessRole.staff,
    AccessRole.logistics,
    AccessRole.maintenance,
  },
};

bool canAccess(AccessRole role, ClubSpace space) {
  final allowed = kAccessMatrix[space];
  return allowed != null && allowed.contains(role);
}

AccessRole accessRoleFor(Role role) => switch (role) {
  Role.vip => AccessRole.vip,
  Role.regular => AccessRole.regular,
  Role.staff => AccessRole.staff,
};

List<ClubSpace> reservableSpaces(Role role) {
  final accessRole = accessRoleFor(role);
  return kAllSpaces.where((space) => canAccess(accessRole, space)).toList();
}
