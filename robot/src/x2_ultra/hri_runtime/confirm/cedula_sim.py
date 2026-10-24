"""Cédula SIMULADA (sin OCR real): lectura determinista de NUIP + nombre.

Puerta de entrada para personal externo/obra sin QR del club: el "OCR" es una
simulación determinista sembrada por la entrada (bytes/ruta/texto), con ruido y
confianza configurables y consenso temporal (≥N lecturas iguales) igual que un
lector real. El resultado es SIEMPRE WORKER con subtipo (logistica |
mantenimiento) y una orden de trabajo simulada {area, task} tomada de una tabla
cuyas áreas son accesibles para ese subtipo (tabla de accesos de la spec):

  WORKER logistica      sí: recepcion_vestibulos, centro_convenciones,
                            almacenes_logistica, estacionamientos
  WORKER mantenimiento  sí: todo lo anterior + salas_juntas, gimnasio_deportes,
                            piscina_spa_humedas, zona_infantil_juvenil,
                            habitaciones_alojamiento, oficinas_administrativas,
                            cuartos_tecnicos
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

NUIP_RE = re.compile(r"^\d{8,12}$")

# Tabla sintética de trabajadores (sin datos personales reales).
CEDULA_WORKERS = (
    {"nuip": "1099002233", "name": "Camila Rojas", "subtype": "logistica"},
    {"nuip": "1099445566", "name": "Edgar Peralta", "subtype": "mantenimiento"},
    {"nuip": "1099778899", "name": "Nubia Cardona", "subtype": "logistica"},
    {"nuip": "1099112244", "name": "Wilmer Quintero", "subtype": "mantenimiento"},
)

WORK_ORDERS = {
    "logistica": (
        ("almacenes_logistica", "recoger suministro de aseo"),
        ("centro_convenciones", "montaje de sillas para evento"),
        ("estacionamientos", "señalizar muelle de carga"),
        ("recepcion_vestibulos", "entregar paquetes en recepción"),
    ),
    "mantenimiento": (
        ("cuartos_tecnicos", "revisión de tableros eléctricos"),
        ("piscina_spa_humedas", "medición de cloro y pH"),
        ("gimnasio_deportes", "ajuste de poleas de máquinas"),
        ("salas_juntas", "cambio de filtro de aire"),
    ),
}

SUBTYPE_AREAS = {
    "logistica": {"recepcion_vestibulos", "centro_convenciones",
                  "almacenes_logistica", "estacionamientos"},
    "mantenimiento": {"recepcion_vestibulos", "centro_convenciones", "salas_juntas",
                      "gimnasio_deportes", "piscina_spa_humedas", "zona_infantil_juvenil",
                      "habitaciones_alojamiento", "oficinas_administrativas",
                      "almacenes_logistica", "cuartos_tecnicos", "estacionamientos"},
}


def work_order_for(subtype: str, key: str) -> dict:
    """Orden de trabajo simulada y determinista; área válida para el subtipo."""
    subtype = subtype if subtype in WORK_ORDERS else "logistica"
    h = int.from_bytes(hashlib.sha256(str(key).encode("utf-8")).digest()[:4], "big")
    area, task = WORK_ORDERS[subtype][h % len(WORK_ORDERS[subtype])]
    assert area in SUBTYPE_AREAS[subtype]
    return {"area": area, "task": task}


def _seed_bytes(cedula) -> bytes:
    if isinstance(cedula, (bytes, bytearray)):
        return bytes(cedula)
    if isinstance(cedula, dict):
        if cedula.get("seed") is not None:
            return str(cedula["seed"]).encode("utf-8")
        return json_dumps_seed(cedula)
    s = str(cedula)
    p = Path(s)
    if p.is_file():
        return p.read_bytes()
    return s.encode("utf-8")


def json_dumps_seed(d: dict) -> bytes:
    import json
    return json.dumps(d, sort_keys=True, default=str).encode("utf-8")


@dataclass
class CedulaConfig:
    noise: float = 0.15          # prob. de corromper un dígito por lectura
    confidence: float = 0.90     # confianza base del OCR simulado
    min_confidence: float = 0.60 # lecturas bajo este umbral no cuentan
    consensus_n: int = 3         # lecturas iguales requeridas
    max_reads: int = 12          # tope de intentos


@dataclass
class CedulaOcrSim:
    """OCR simulado determinista: misma entrada + config → mismas lecturas."""
    seed: bytes
    cfg: CedulaConfig = field(default_factory=CedulaConfig)

    def __post_init__(self):
        h = hashlib.sha256(self.seed).digest()
        base = CEDULA_WORKERS[int.from_bytes(h[:4], "big") % len(CEDULA_WORKERS)]
        self.truth = dict(base)
        self._rng = random.Random(h)

    @classmethod
    def for_input(cls, cedula, cfg: CedulaConfig | None = None) -> "CedulaOcrSim":
        cfg = cfg or CedulaConfig()
        sim = cls(_seed_bytes(cedula), cfg)
        if isinstance(cedula, dict):
            if NUIP_RE.match(str(cedula.get("nuip") or "")):
                sim.truth["nuip"] = str(cedula["nuip"])
            if cedula.get("name"):
                sim.truth["name"] = str(cedula["name"])
            if cedula.get("subtype") in WORK_ORDERS:
                sim.truth["subtype"] = cedula["subtype"]
        elif isinstance(cedula, str) and NUIP_RE.match(cedula):
            sim.truth["nuip"] = cedula  # NUIP aportado: el nombre sale de la tabla
        return sim

    def read(self, i: int) -> dict:
        """Una lectura (frame) simulada con ruido/confianza deterministas."""
        rng = random.Random(hashlib.sha256(self.seed + i.to_bytes(2, "big")).digest())
        nuip, name = self.truth["nuip"], self.truth["name"]
        if rng.random() < self.cfg.noise and len(nuip) > 1:
            pos = rng.randrange(len(nuip))
            nuip = nuip[:pos] + str(rng.randrange(10)) + nuip[pos + 1:]
        conf = max(0.0, min(1.0, self.cfg.confidence + rng.uniform(-0.15, 0.05)))
        if rng.random() < self.cfg.noise * 0.5:
            name = name.split(" ")[0]  # apellido perdido en la lectura ruidosa
        return {"t": round(i * 0.25, 3), "nuip": nuip, "name": name, "confidence": round(conf, 3)}

    def run(self) -> dict:
        """Acumula lecturas hasta consenso temporal (≥consensus_n iguales)."""
        counts: dict[tuple, int] = {}
        readings = []
        for i in range(self.cfg.max_reads):
            r = self.read(i)
            readings.append(r)
            if r["confidence"] >= self.cfg.min_confidence:
                k = (r["nuip"], r["name"])
                counts[k] = counts.get(k, 0) + 1
                if counts[k] >= self.cfg.consensus_n and NUIP_RE.match(r["nuip"]):
                    return {"ok": True, "nuip": r["nuip"], "name": r["name"],
                            "subtype": self.truth.get("subtype"),
                            "confidence": r["confidence"], "reads_used": i + 1,
                            "readings": readings, "reason": None}
        reason = ("CEDULA_LOW_CONFIDENCE"
                  if all(r["confidence"] < self.cfg.min_confidence for r in readings)
                  else "CEDULA_CONSENSUS_FAIL")
        return {"ok": False, "nuip": None, "name": None, "confidence": None,
                "reads_used": len(readings), "readings": readings, "reason": reason}


def consensus_over(readings: list[dict], cfg: CedulaConfig | None = None) -> dict:
    """Consenso temporal sobre lecturas ya dadas (p. ej. simuladas por T1)."""
    cfg = cfg or CedulaConfig()
    counts: dict[tuple, int] = {}
    for i, r in enumerate(readings):
        if float(r.get("confidence", 1.0)) < cfg.min_confidence:
            continue
        k = (str(r.get("nuip")), str(r.get("name")))
        counts[k] = counts.get(k, 0) + 1
        if counts[k] >= cfg.consensus_n and NUIP_RE.match(k[0] or ""):
            return {"ok": True, "nuip": k[0], "name": k[1],
                    "confidence": r.get("confidence"), "reads_used": i + 1,
                    "readings": list(readings[:i + 1]), "reason": None}
    return {"ok": False, "nuip": None, "name": None, "confidence": None,
            "reads_used": len(readings), "readings": list(readings),
            "reason": "CEDULA_CONSENSUS_FAIL"}
