"""Tabla de accesos (sí/no) del Club N — exacta según docs/reto-2/HRI_RUNTIME_SPEC.md.

Clases: VIP, REGULAR (socio), GUEST (invitado), STAFF, WORKER (subtipo
``logistica`` | ``mantenimiento``). EXTERNAL se normaliza a WORKER.
"""
from __future__ import annotations

from typing import Optional

RANKS = ("VIP", "REGULAR", "GUEST", "STAFF", "WORKER")
WORKER_SUBTYPES = ("logistica", "mantenimiento")

# Orden y valores exactos de la tabla del spec. Columnas:
# (VIP, REGULAR, GUEST, STAFF, WORKER.logistica, WORKER.mantenimiento)
_T = True
_F = False
_TABLE: dict[str, tuple[bool, bool, bool, bool, bool, bool]] = {
    "recepcion_vestibulos":       (_T, _T, _T, _T, _T, _T),
    "restaurantes_cafeterias":    (_T, _T, _T, _T, _F, _F),
    "bares_la_cava":              (_T, _T, _F, _F, _F, _F),
    "centro_convenciones":        (_T, _T, _T, _T, _T, _T),
    "salas_juntas":               (_T, _T, _F, _T, _F, _T),
    "gastroworking":              (_T, _T, _F, _T, _F, _F),
    "gimnasio_deportes":          (_T, _T, _F, _F, _F, _T),
    "piscina_spa_humedas":        (_T, _T, _F, _F, _F, _T),
    "biblioteca_galeria":         (_T, _T, _T, _T, _F, _F),
    "zona_infantil_juvenil":      (_T, _T, _F, _T, _F, _T),
    "habitaciones_alojamiento":   (_T, _F, _F, _T, _F, _T),
    "oficinas_administrativas":   (_F, _F, _F, _T, _F, _T),
    "almacenes_logistica":        (_F, _F, _F, _T, _T, _T),
    "cuartos_tecnicos":           (_F, _F, _F, _T, _F, _T),
    "estacionamientos":           (_T, _T, _T, _T, _T, _T),
}
SPACES: tuple[str, ...] = tuple(_TABLE)

# Espacios "equivalentes" usados para sugerir una alternativa cuando el acceso
# es denegado (primero de la lista que la persona sí pueda visitar). Primero una experiencia del club (biblioteca,
# gastroworking, convenciones…), no la recepción: la negación empieza por lo que sí puede vivir.
_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "bares_la_cava": ("restaurantes_cafeterias", "gastroworking"),
    "salas_juntas": ("gastroworking", "centro_convenciones"),
    "gastroworking": ("biblioteca_galeria", "restaurantes_cafeterias"),
    "gimnasio_deportes": ("piscina_spa_humedas", "centro_convenciones"),
    "piscina_spa_humedas": ("gimnasio_deportes", "centro_convenciones"),
    "zona_infantil_juvenil": ("biblioteca_galeria", "centro_convenciones"),
    "habitaciones_alojamiento": ("biblioteca_galeria", "piscina_spa_humedas", "gastroworking", "centro_convenciones"),
    "oficinas_administrativas": ("salas_juntas", "gastroworking", "biblioteca_galeria", "centro_convenciones"),
    "almacenes_logistica": ("centro_convenciones", "biblioteca_galeria", "estacionamientos"),
    "cuartos_tecnicos": ("centro_convenciones", "biblioteca_galeria", "almacenes_logistica"),
    "restaurantes_cafeterias": ("centro_convenciones", "recepcion_vestibulos", "estacionamientos"),
    "biblioteca_galeria": ("centro_convenciones", "recepcion_vestibulos"),
}


def normalize_rank(rank: str, worker_subtype: Optional[str] = None) -> tuple[str, Optional[str]]:
    """EXTERNAL ⇒ WORKER (spec). Devuelve (rank, worker_subtype|None)."""
    r = (rank or "").strip().upper()
    if r in ("EXTERNAL", "WORKER"):
        st = (worker_subtype or "logistica").strip().lower()
        if st not in WORKER_SUBTYPES:
            st = "logistica"
        return "WORKER", st
    if r not in RANKS:
        raise ValueError(f"rango desconocido: {rank!r}")
    return r, None


def allowed(space: str, rank: str, worker_subtype: Optional[str] = None) -> bool:
    """¿La persona puede pasar a `space`?"""
    if space not in _TABLE:
        raise ValueError(f"espacio desconocido: {space!r}")
    r, st = normalize_rank(rank, worker_subtype)
    col = {"VIP": 0, "REGULAR": 1, "GUEST": 2, "STAFF": 3}[r] if r != "WORKER" else (4 if st == "logistica" else 5)
    return _TABLE[space][col]


def allowed_spaces(rank: str, worker_subtype: Optional[str] = None) -> list[str]:
    return [s for s in SPACES if allowed(s, rank, worker_subtype)]


def alternative(space: str, rank: str, worker_subtype: Optional[str] = None) -> Optional[str]:
    """Primer espacio alternativo que la persona sí puede visitar (para la negación cortés)."""
    for cand in _ALTERNATIVES.get(space, ("recepcion_vestibulos", "estacionamientos")):
        if cand != space and allowed(cand, rank, worker_subtype):
            return cand
    return None


def access_row(space: str) -> dict[str, bool]:
    v = _TABLE[space]
    return {"VIP": v[0], "REGULAR": v[1], "GUEST": v[2], "STAFF": v[3],
            "WORKER_logistica": v[4], "WORKER_mantenimiento": v[5]}
