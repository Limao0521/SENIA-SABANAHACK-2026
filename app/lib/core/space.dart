enum ClubSpace {
  recepcion,
  restaurantes,
  bares,
  convenciones,
  salasJuntas,
  gastroworking,
  gimnasio,
  piscina,
  biblioteca,
  zonaInfantil,
  habitaciones,
  oficinas,
  almacenes,
  cuartosTecnicos,
  estacionamientos,
}

extension ClubSpaceName on ClubSpace {
  String get displayName => switch (this) {
    ClubSpace.recepcion => 'Recepción y vestíbulos',
    ClubSpace.restaurantes => 'Restaurantes y cafeterías',
    ClubSpace.bares => 'Bares y La Cava',
    ClubSpace.convenciones => 'Centro de Convenciones',
    ClubSpace.salasJuntas => 'Salas de juntas',
    ClubSpace.gastroworking => 'Gastroworking',
    ClubSpace.gimnasio => 'Gimnasio y deportes',
    ClubSpace.piscina => 'Piscina, spa y zonas húmedas',
    ClubSpace.biblioteca => 'Biblioteca y galería',
    ClubSpace.zonaInfantil => 'Zona infantil y juvenil',
    ClubSpace.habitaciones => 'Habitaciones de alojamiento',
    ClubSpace.oficinas => 'Oficinas administrativas',
    ClubSpace.almacenes => 'Almacenes y logística',
    ClubSpace.cuartosTecnicos => 'Cuartos técnicos y maquinaria',
    ClubSpace.estacionamientos => 'Estacionamientos',
  };
}

const List<ClubSpace> kAllSpaces = ClubSpace.values;
