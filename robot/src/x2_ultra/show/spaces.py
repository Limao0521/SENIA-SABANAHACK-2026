"""La biblioteca de espacios del Club N (``hri_runtime/club_kb.json``, de Luis) llevada al club 3D.

La KB manda: los 15 espacios, sus pisos, horarios y quién puede entrar (``hri_runtime.access``). Aquí solo se decide
cómo se llega a cada uno desde la recepción del club 3D (``show.club``):

- ``aqui``: la propia recepción y los vestíbulos.
- ``ascensores``: el pasillo y el vestíbulo de ascensores; desde allí el ascensor sube o baja al piso de la KB
  (1 es el propio vestíbulo de ascensores, donde está La Cava).
- ``personal``: la puerta SOLO PERSONAL (al sur), con la escalera a los cuartos técnicos del sótano.
- ``servicio``: el ACCESO DE SERVICIO (al norte), con la bodega y la recepción de proveedores.

La dirección hacia la que señala el robot sale de esa ruta, no de la ``direccion`` que trae la KB: así el gesto, la
frase («queda a mi izquierda») y el camino que hacen el robot o el perro D1 coinciden. ``kb()`` devuelve la KB con
esas direcciones para dársela a la FSM de Luis.
"""
from __future__ import annotations

import copy
from functools import lru_cache

from x2_ultra.hri_runtime import contract

ROUTE = {"recepcion_vestibulos": "aqui", "almacenes_logistica": "servicio", "cuartos_tecnicos": "personal"}
DIRECTION = {"aqui": "front", "ascensores": "left", "personal": "right", "servicio": "front"}
FLOOR_ORDER = ("6", "5", "4", "3", "2", "1", "S1", "S2")     # el directorio de pisos, de arriba abajo
SHORT = {                                                     # nombres cortos para el directorio de pisos
    "recepcion_vestibulos": "Recepción", "restaurantes_cafeterias": "Restaurantes", "bares_la_cava": "La Cava",
    "centro_convenciones": "Convenciones", "salas_juntas": "Salas de juntas", "gastroworking": "Gastroworking",
    "gimnasio_deportes": "Gimnasio", "piscina_spa_humedas": "Piscina y spa", "biblioteca_galeria": "Biblioteca",
    "zona_infantil_juvenil": "Zona infantil", "habitaciones_alojamiento": "Habitaciones",
    "oficinas_administrativas": "Oficinas", "almacenes_logistica": "Almacenes", "cuartos_tecnicos": "Cuartos técnicos",
    "estacionamientos": "Estacionamientos",
}


@lru_cache(maxsize=1)
def _kb() -> dict:
    return contract.load_kb()


def kb() -> dict:
    """La KB de Luis con la ``direccion`` de cada espacio según su ruta en el club 3D (copia: se puede modificar)."""
    out = copy.deepcopy(_kb())
    for space, info in out["espacios"].items():
        info["direccion"] = direction(space)
    return out


def identity() -> dict:
    """La identidad del Club N (lema, valores, voz…) de la KB."""
    return _kb().get("identidad") or {}


def spaces() -> dict:
    return _kb()["espacios"]


def route(space: str) -> str:
    if space not in spaces():
        raise ValueError(f"espacio desconocido: {space!r}")
    return ROUTE.get(space, "ascensores")


def direction(space: str) -> str:
    """right | left | front, como en la KB (``contract.DIRECTION_TO_TYPE``)."""
    return DIRECTION[route(space)]


def floor(space: str) -> str:
    return str(spaces()[space]["piso"])


def name(space: str) -> str:
    return str(spaces()[space]["nombre"])


def elevator_for(floor_label) -> int:
    """Ascensor de la izquierda (0) para los pisos pares y la derecha (1) para los impares (S1, S2 como 1, 2)."""
    digits = "".join(c for c in str(floor_label) if c.isdigit())
    return 0 if digits and int(digits) % 2 == 0 else 1


def floors() -> dict[str, list[str]]:
    """Piso → espacios de la KB en ese piso, en el orden del directorio."""
    out: dict[str, list[str]] = {f: [] for f in FLOOR_ORDER}
    for space, info in spaces().items():
        out.setdefault(str(info["piso"]), []).append(space)
    return {f: s for f, s in out.items() if s}


def directory_rows() -> list[tuple[str, str]]:
    return [(f, " · ".join(SHORT.get(s, name(s)) for s in ss)) for f, ss in floors().items()]


def floor_names() -> dict[int, str]:
    """Pisos 2–6 (los que el recorrido guiado de la ventana ofrece) con lo que hay en cada uno según la KB."""
    out = {}
    for f, ss in floors().items():
        if f.isdigit() and int(f) >= 2:
            names = [SHORT.get(s, name(s)) for s in ss]
            out[int(f)] = ", ".join(names)
    return dict(sorted(out.items()))
