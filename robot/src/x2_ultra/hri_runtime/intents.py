"""Intenciones de la conversación (spec FSM) + clasificador/emparejador de respaldo.

El LLM clasifica con el json_schema de `contract.py`; estas funciones dan un
respaldo determinista (regex/keywords) cuando el LLM falla o no está disponible,
y se usan en tests.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from .access import SPACES

INTENTS = ("PREGUNTA", "PIDE_DESTINO", "SI", "NO", "DESPEDIDA", "OTRO")

# Keywords por espacio (sin tildes, minúsculas) para emparejar área mencionada.
AREA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "recepcion_vestibulos": ("recepcion", "vestibulo", "mostrador", "entrada principal"),
    "restaurantes_cafeterias": ("restaurante", "cafeteria", "cafe", "comer", "almorzar", "desayuno", "terraza"),
    "bares_la_cava": ("bar", "cava", "bares", "trago", "coctel", "vino"),
    "centro_convenciones": ("convenciones", "auditorio", "congreso", "evento", "salon principal"),
    "salas_juntas": ("sala de juntas", "salas de juntas", "juntas", "reunion", "reuniones", "sala de reunion"),
    "gastroworking": ("gastroworking", "coworking", "trabajar", "trabajo compartido"),
    "gimnasio_deportes": ("gimnasio", "deporte", "deportes", "entrenar", "squash", "cancha", "gym", "fit"),
    "piscina_spa_humedas": ("piscina", "spa", "sauna", "turco", "humedas", "humeda", "nadar", "masaje"),
    "biblioteca_galeria": ("biblioteca", "galeria", "leer", "lectura", "libros", "arte"),
    "zona_infantil_juvenil": ("infantil", "juvenil", "ninos", "nino", "ludoteca", "videojuegos", "hijos"),
    "habitaciones_alojamiento": ("habitacion", "habitaciones", "alojamiento", "cuarto", "suite", "suites", "dormir", "hospedaje"),
    "oficinas_administrativas": ("oficina", "oficinas", "administracion", "administrativas", "gerencia", "tesoreria",
                                 "cuarto del staff", "cuartos del staff", "zona del staff", "area del staff",
                                 "cuarto de empleados", "back office"),
    "almacenes_logistica": ("almacen", "almacenes", "logistica", "bodega", "bodegas", "proveedor", "proveedores", "insumos", "dotacion"),
    "cuartos_tecnicos": ("cuarto tecnico", "cuartos tecnicos", "sala de maquinas", "maquinas", "tableros", "planta electrica", "climatizacion", "mantenimiento tecnico"),
    "estacionamientos": ("estacionamiento", "estacionamientos", "parqueadero", "parqueaderos", "parking", "garaje", "carro", "coche", "valet"),
}

_SI_RE = re.compile(r"(?<!\w)(si|sí|claro|por supuesto|vale|de acuerdo|listo|afirmativo|me parece|dale|ok)(?!\w)")
_NO_RE = re.compile(r"(?<!\w)(no|negativo|mejor no|no gracias|nop)(?!\w)")
_DESPEDIDA_RE = re.compile(
    r"(?<!\w)(adios|adiós|hasta luego|hasta pronto|hasta la proxima|nos vemos|chao|chau|me voy|me retiro|ya me fui|buenas noches y|que tengas buen|cuidate|cuídese)(?!\w)")
_DESTINO_RE = re.compile(
    r"(?<!\w)(ir a|ir al|voy a|quiero ir|puedo ir|podria ir|gustaria ir|necesito ir|llevarme|acompa[nñ]a(?:rme|me)?|gu[ií]a(?:rme|me)?|indicame|ind[ií]came|d[oó]nde queda|donde queda|c[oó]mo llego|como llego|me permite pasar|puedo pasar|entrar a|acceso a|direcci[oó]n de)(?!\w)")
_PREGUNTA_RE = re.compile(r"(?<!\w)(qu[eé]|cu[aá]l|cu[aá]ndo|cuando|qui[eé]n|quien|c[oó]mo|como|a qu[eé] hora|en qu[eé] piso|d[oó]nde|donde|por qu[eé]|porque|hay |tienen |sirven|ofrecen)(?!\w)|\?|¿")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def match_area(text: str) -> Optional[str]:
    """Espacio de la tabla mencionado en el texto (keywords), o None."""
    t = _norm(text)
    best: Optional[tuple[int, str]] = None
    for space in SPACES:
        for kw in AREA_KEYWORDS[space]:
            # plurales regulares: restaurante(s), almacen(es), cafe(s), bar(es)...
            m = re.search(r"(?<!\w)" + re.escape(_norm(kw)) + r"(?:s|es)?(?!\w)", t)
            if m:
                # gana la keyword más larga (más específica): "sala de juntas" > "sala"
                if best is None or len(kw) > best[0]:
                    best = (len(kw), space)
    return best[1] if best else None


_INFO_RE = re.compile(
    r"(?<!\w)(dame informacion|informacion (?:acerca|sobre|de)|cuentame|hablame|que opciones|que hay|a donde podria ir|donde podria ir|que opciones hay)(?!\w)")
# verbo explícito de desplazamiento: sólo con uno de éstos una pregunta se reconcilia a PIDE_DESTINO
_GO_VERB_RE = re.compile(
    r"(?<!\w)(voy a|quiero ir|puedo ir|podria ir|gustaria ir|necesito ir|ir a|ir al|llevarme|acompana(?:rme|me)?|gu[ií]a(?:rme|me)?)(?!\w)")


def fallback_intent(text: str) -> tuple[str, Optional[str]]:
    """Clasificación determinista de respaldo → (intent, area|None)."""
    t = (text or "").strip()
    area = match_area(t)
    if _DESPEDIDA_RE.search(_norm(t)) and not _DESTINO_RE.search(_norm(t)):
        return "DESPEDIDA", None
    if _INFO_RE.search(_norm(t)):       # pedir información ⇒ PREGUNTA (aunque cite un espacio)
        return "PREGUNTA", area
    if _DESTINO_RE.search(_norm(t)) and area:
        return "PIDE_DESTINO", area
    if re.fullmatch(r"\s*[¡!¿?]?\s*(no|negativo|mejor no|no,?\s+gracias)\s*[.!?…]?\s*", _norm(t)):
        return "NO", None
    if re.fullmatch(r"\s*[¡!¿?]?\s*(si|sí|claro|por supuesto|vale|de acuerdo|listo|dale|ok|me parece perfecto)\s*[.!?…]?\s*", _norm(t)):
        return "SI", None
    if _NO_RE.search(_norm(t)) and len(t.split()) <= 4:
        return "NO", None
    if _SI_RE.search(_norm(t)) and len(t.split()) <= 4:
        return "SI", None
    if area and _PREGUNTA_RE.search(t):
        return "PREGUNTA", area
    if "?" in t or "¿" in t:
        return "PREGUNTA", area
    if area:
        return "PIDE_DESTINO", area
    return "OTRO", None
