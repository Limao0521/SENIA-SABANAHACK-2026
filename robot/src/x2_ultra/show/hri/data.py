"""Datos precomputados de HRIEGO para la recepción del reto 2 (solo lectura; nada se genera aquí).

Fuentes (checkout hermano, de solo lectura):
- ``HRIEGO_gesture/visits_v2/<rank>/``: ``visit_log.json`` (turnos GREETING → GUIDANCE → DIALOGUE×3 →
  FAREWELL con habla, emote y disparador), ``wav/`` (Supertonic 44,1 kHz),
  ``sonic/<turn>/<turn>_arms.npz`` (brazos SONIC + waist/head a 50 Hz; cuadro 100 = t0 del audio) y
  ``anchor_plan/`` (anclas CTC y planes de dispatch).
- ``HRIEGO_gesture/contract/catalog.json``: emote → (motion_id, area) o señalamiento externo.
- ``config/preset_latency_c08.json``: lat medidas en el gemelo (lead_onset, lead_stroke, duración).
- ``animation_player.yaml`` de mc_param (AgiBot): (motion_id, area) → CSV de animación.

Convención de rangos: HRIEGO VIP/REGULAR/GUEST/STAFF/EXTERNAL ↔ club vip/socio/invitado/personal/externo.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Raíces por defecto RELATIVAS AL REPO (datos versionados en assets/hri/, ver su PROVENANCE.md);
# las variables de entorno siguen permitiendo apuntar a checkouts hermanos.
REPO_ROOT = Path(__file__).resolve().parents[4]
HRIEGO_ROOT = Path(os.environ.get("X2_HRIEGO_ROOT", str(REPO_ROOT / "assets" / "hri" / "HRIEGO_gesture")))
# Assets del MC de AgiBot (no versionados, licencia): los copia scripts/fetch_hri_assets.sh.
MC_CLASSIC = Path(os.environ.get("X2_MC_CLASSIC", str(REPO_ROOT / "assets" / "hri" / "mc_classic")))
IDENTITIES = Path(os.environ.get("HRI_IDENTITIES", str(REPO_ROOT / "assets" / "hri" / "identities.json")))

RANK_BY_CATEGORY = {"vip": "vip", "socio": "regular", "invitado": "guest", "personal": "staff",
                    "externo": "external"}
# Dirección del señalamiento hacia cada ruta, vista desde el punto de recepción (el robot mira a +x,
# la entrada al este): los ascensores quedan al norte (+y, izquierda), la puerta de personal al sur
# (-y, derecha) y el acceso de servicio al norte junto a la entrada (frente). Coherente con
# llm_script.scenario_for_club.
ROUTE_DIRECTION = {"vip": "LEFT", "socio": "LEFT", "invitado": "LEFT", "personal": "RIGHT", "externo": "FRONT"}
DIRECTION_EMOTE = {"LEFT": "POINT_LEFT", "RIGHT": "POINT_RIGHT", "FRONT": "POINT_FRONT"}
# Turno GUIDANCE prestado cuando el del propio rango señala a otro lado: la dirección hablada y la
# gestual deben coincidir con la ruta real del club (validación «pointing toward the correct route»).
_GUIDANCE_DONOR = {"LEFT": "regular", "RIGHT": "vip", "FRONT": "guest"}


@dataclass(frozen=True)
class TurnData:
    turn_id: str
    rank: str
    state: str
    emote: str                    # GREETING_VIP, POINT_RIGHT, APPLAUSE, NONE, ...
    speech: str
    wav: Path
    sample_rate: int
    duration_s: float
    anchor_start_s: float | None  # inicio del disparador en el audio (CTC)
    anchor_end_s: float | None
    plan_status: str              # planned | anchor_only_no_preset | none_sonic
    delay_s: float                # retardo del inicio de audio respecto al dispatch del preset
    sonic: Path | None            # npz de brazos SONIC (todos los turnos de visits_v2 lo tienen)
    borrowed: bool = False        # GUIDANCE prestado de otro rango (dirección compatible)
    extra: dict = field(default_factory=dict)

    @property
    def is_point(self) -> bool:
        return self.emote.startswith("POINT_")

    @property
    def direction(self) -> str | None:
        return self.emote.split("_", 1)[1] if self.is_point else None


def _parse_animation_yaml(path: Path, base: Path | None = None) -> dict[tuple[int, int], Path]:
    """(motion_id, area) → CSV, con un parser de expresiones regulares (el formato es regular)."""
    table: dict[tuple[int, int], Path] = {}
    motion = area = None
    base = Path(base) if base is not None else MC_CLASSIC
    for line in path.read_text().splitlines():
        m = re.match(r"\s*-?\s*animation:\s*(\d+)", line)
        if m:
            motion = int(m.group(1))
            continue
        m = re.match(r"\s*-?\s*area:\s*(\d+)", line)
        if m and motion is not None:
            area = int(m.group(1))
            continue
        m = re.search(r'path:\s*"\./classic/animation/([^"]+)"', line)
        if m and motion is not None and area is not None:
            table[(motion, area)] = base / "animation" / m.group(1)
    return table


class VisitStore:
    """Acceso de solo lectura a los datos precomputados de un conjunto de visitas."""

    def __init__(self, root: Path = HRIEGO_ROOT, mc_classic: Path = MC_CLASSIC):
        self.root = Path(root)
        self.catalog = json.loads((self.root / "contract" / "catalog.json").read_text())["emotes"]
        yaml_path = Path(mc_classic) / "animation_player.yaml"
        # Sin los CSV del MC (scripts/fetch_hri_assets.sh) la tienda funciona, pero los presets
        # cinemáticos quedan indisponibles (csv_for → None) y el reproductor usa solo SONIC.
        self.animations = (_parse_animation_yaml(yaml_path, Path(mc_classic))
                           if yaml_path.is_file() else {})
        lat_path = self.root / "config" / "preset_latency_c08.json"
        self.latency = json.loads(lat_path.read_text())["bindings"] if lat_path.is_file() else {}
        self._visits: dict[str, list[TurnData]] = {}

    # -------------------------------------------------------------------------------- turnos
    def _visits_dir(self, rank: str) -> Path:
        """Directorio de datos de la visita. Por defecto ``visits_v2/<rank>``; la variable de
        entorno ``HRI_VISITS_ROOT`` apunta a una raíz por persona (p. ej.
        ``HRIEGO_gesture/opt/realnames/visits/<persona>``, que espeja el formato de
        ``visits_v2/<rank>/``) y sustituye a todos los rangos (una persona = una visita)."""
        override = os.environ.get("HRI_VISITS_ROOT")
        if override:
            return Path(override)
        return self.root / "visits_v2" / rank

    def turns(self, rank: str) -> list[TurnData]:
        if rank not in self._visits:
            visits_dir = self._visits_dir(rank)
            log = json.loads((visits_dir / "visit_log.json").read_text())
            out = []
            for t in log["turns"]:
                decision = t["decision"]
                tts = t["tts"]
                plan = t.get("plan") or {}
                wav = self.root / tts["wav_path"]
                sonic = visits_dir / "sonic" / t["turn_id"] / f"{t['turn_id']}_arms.npz"
                out.append(TurnData(
                    turn_id=t["turn_id"], rank=rank, state=t["state"],
                    emote=decision["effective"], speech=t["llm"]["parsed"]["speech"],
                    wav=wav, sample_rate=int(tts["sample_rate"]), duration_s=float(tts["duration_s"]),
                    anchor_start_s=plan.get("trigger_start_s"), anchor_end_s=plan.get("trigger_end_s"),
                    plan_status=plan.get("status") or "none_sonic", delay_s=float(plan.get("delay_s") or 0.0),
                    sonic=sonic if sonic.is_file() else None,
                    extra={"trigger_text": decision.get("trigger_text")}))
            self._visits[rank] = out
        return self._visits[rank]

    def turn(self, rank: str, turn_id: str) -> TurnData:
        return next(t for t in self.turns(rank) if t.turn_id == turn_id)

    def guidance_for(self, category: str) -> TurnData:
        """Turno GUIDANCE cuya dirección hablada coincide con la ruta del club para ``category``."""
        rank = RANK_BY_CATEGORY[category]
        direction = ROUTE_DIRECTION[category]
        emote = DIRECTION_EMOTE[direction]
        own = next(t for t in self.turns(rank) if t.state == "GUIDANCE")
        if own.emote == emote:
            return own
        donor = _GUIDANCE_DONOR[direction]
        return next(t for t in self.turns(donor) if t.state == "GUIDANCE" and t.emote == emote
                    and not t.borrowed)

    def visit_plan(self, category: str) -> list[TurnData]:
        """Los 6 turnos de la visita del rango: GREETING, GUIDANCE (dirección de la ruta), 3 DIALOGUE,
        FAREWELL."""
        rank = RANK_BY_CATEGORY[category]
        plan = []
        for t in self.turns(rank):
            if t.state == "GUIDANCE":
                g = self.guidance_for(category)
                if g is not t:
                    from dataclasses import replace
                    g = replace(g, borrowed=True, extra={**g.extra, "borrowed_from": g.rank})
                plan.append(g)
            else:
                plan.append(t)
        return plan

    # -------------------------------------------------------------------------------- assets
    def emote_binding(self, emote: str) -> tuple[int, int] | None:
        e = self.catalog.get(emote)
        if not e or e.get("motion_id") is None:
            return None
        return int(e["motion_id"]), int(e["area"])

    def csv_for(self, emote: str) -> Path | None:
        binding = self.emote_binding(emote)
        if binding is None:
            return None
        return self.animations.get(binding)

    def stroke_offset(self, emote: str) -> float:
        """Del arranque del CSV al stroke visible. La reproducción es *cinemática* (el CSV arranca
        de una vez, sin preplay del MC), así que el stroke cae en ``stroke_s`` de la cronología CSV
        de c08 (mismo detector oficial: 90 % del desplazamiento máximo de la articulación dominante
        respecto a reposo). El respaldo ``lead_stroke − lead_onset`` (latencias MC de c08) subestima
        ese instante +110…+200 ms sistemáticamente (y >1 s en NEGATION_NORMAL, dominado por cabeza)."""
        binding = self.emote_binding(emote)
        key = f"{binding[0]}/{binding[1]}" if binding else None
        info = self.csv_timeline().get(key) if key else None
        if info and info.get("stroke_s") is not None:
            try:
                return float(info["stroke_s"])
            except (TypeError, ValueError):
                pass
        info = self.latency.get(key) if key else None
        if info:
            try:
                return float(info["lead_stroke_s"]["median"]) - float(info["lead_onset_s"]["median"])
            except (KeyError, TypeError, ValueError):
                pass
        return 0.55      # respaldo genérico

    def preset_duration(self, emote: str, csv_duration: float) -> float:
        binding = self.emote_binding(emote)
        key = f"{binding[0]}/{binding[1]}" if binding else None
        info = self.latency.get(key) if key else None
        if info and "motion_duration_s" in info:
            try:
                return float(info["motion_duration_s"]["median"])
            except (TypeError, ValueError):
                pass
        return csv_duration

    def csv_timeline(self) -> dict:
        """``preset_csv_timeline.json`` de c08: por binding, articulación dominante, postura de
        reposo y cronometría del CSV (onset/stroke al 90 % del desplazamiento máximo)."""
        if not hasattr(self, "_timeline"):
            path = self.root / "twin" / "replay" / "presets_c08" / "preset_csv_timeline.json"
            self._timeline = json.loads(path.read_text()) if path.is_file() else {}
        return self._timeline

    def stroke_probe(self, emote: str) -> tuple[str, float] | None:
        """(articulación dominante, valor de reposo) para medir el stroke como en c08/c12."""
        binding = self.emote_binding(emote)
        key = f"{binding[0]}/{binding[1]}" if binding else None
        info = self.csv_timeline().get(key) if key else None
        if not info:
            return None
        jd = info.get("dominant_joint")
        rest = (info.get("rest_q") or {}).get(jd)
        if jd is None or rest is None:
            return None
        return str(jd), float(rest)

    def point_targets(self) -> dict:
        return json.loads((self.root / "twin" / "point_targets.json").read_text())


def load_identities(path: Path | None = None) -> dict:
    """Identidades precomputadas (persona → rango/categoría/piso), sin datos biométricos."""
    return json.loads(Path(path or IDENTITIES).read_text(encoding="utf-8"))["people"]


def use_person_visits(person: str, root: Path | None = None) -> Path:
    """Apunta ``HRI_VISITS_ROOT`` al paquete de visita realnames versionado de ``person``."""
    entry = load_identities()[person]
    visits = Path(root or HRIEGO_ROOT) / entry["visits"]
    os.environ["HRI_VISITS_ROOT"] = str(visits)
    return visits
