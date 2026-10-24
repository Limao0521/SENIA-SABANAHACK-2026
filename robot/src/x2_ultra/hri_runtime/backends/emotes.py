"""Emotes cinemáticos propios: respaldo cuando faltan los CSV de animación de AgiBot.

Los CSV del MC (``assets/hri/mc_classic``, licencia del fabricante) no están en el robot ni en el
PC del equipo (2026-10-24), así que ``AimdkBackend`` registraba ``emote_unavailable`` y el robot no
saludaba ni se despedía. Aquí cada emote del catálogo de HRIEGO (``contract/catalog.json``) tiene
una pista de fotogramas clave de brazos con la MISMA interfaz que ``show.hri.motion.CsvPreset``
(``duration`` y ``sample(t) -> (arms14, waist, head)``), así que el backend la reproduce por URS con
sus mezclas de entrada/salida, el limitador y el interlock.

Convenciones (verificadas con ``HRIEGO_gesture/twin/point_targets.json``): ``shoulder_pitch`` < 0
levanta el brazo hacia delante; ``shoulder_roll`` abre el brazo hacia fuera (derecho < 0, izquierdo
> 0); codo < 0 flexiona. Sin cintura (bloqueada) ni cabeza. Velocidades pico ≈ 1-3 rad/s.
"""
from __future__ import annotations

import math

import numpy as np

from x2_ultra.robot import joints as J

# READY de AimdkBackend (mismo orden que J.ARM_JOINTS: izquierdo 7 + derecho 7)
READY = np.array([0.2, 0.2, 0.0, -0.6, 0.0, 0.0, 0.0, 0.2, -0.2, 0.0, -0.6, 0.0, 0.0, 0.0])
_IDX = {n: i for i, n in enumerate(J.ARM_JOINTS)}


def _pose(base=READY, **kw) -> np.ndarray:
    """Pose de brazos a partir de READY: claves cortas l_/r_ + pitch|roll|yaw|elbow|wyaw|wpitch|wroll."""
    names = {"pitch": "shoulder_pitch", "roll": "shoulder_roll", "yaw": "shoulder_yaw", "elbow": "elbow",
             "wyaw": "wrist_yaw", "wpitch": "wrist_pitch", "wroll": "wrist_roll"}
    q = np.array(base, dtype=float)
    for k, v in kw.items():
        side, part = k.split("_", 1)
        q[_IDX[f"{'left' if side == 'l' else 'right'}_{names[part]}_joint"]] = float(v)
    return q


class KeyframePreset:
    """Pista de fotogramas clave (t, brazos14) con interpolación suave (smoothstep por tramo)."""

    def __init__(self, name: str, keys: list[tuple[float, np.ndarray]]):
        self.name = name
        self.t = np.array([k[0] for k in keys], dtype=float)
        self.q = np.stack([k[1] for k in keys])
        self.duration = float(self.t[-1])
        lo = np.array([J.JOINT_RANGE[n][0] + 0.05 for n in J.ARM_JOINTS])
        hi = np.array([J.JOINT_RANGE[n][1] - 0.05 for n in J.ARM_JOINTS])
        self.q = np.clip(self.q, lo, hi)

    def sample(self, t: float):
        tt = float(np.clip(t, self.t[0], self.t[-1]))
        i = int(np.searchsorted(self.t, tt, side="right")) - 1
        i = min(max(i, 0), len(self.t) - 2)
        t0, t1 = self.t[i], self.t[i + 1]
        u = 0.0 if t1 <= t0 else (tt - t0) / (t1 - t0)
        u = u * u * (3.0 - 2.0 * u)
        arms = self.q[i] + u * (self.q[i + 1] - self.q[i])
        return arms, np.zeros(0), np.zeros(0)


def _osc(name: str, base: np.ndarray, joint: str, amp: float, hz: float, cycles: float,
         lead: float = 0.6, hold: float = 0.3) -> KeyframePreset:
    """Sube a ``base`` en ``lead`` s, oscila ``joint`` ±amp y mantiene ``hold`` s al final."""
    keys = [(0.0, READY.copy()), (lead, base.copy())]
    n = int(round(cycles * 4))
    for k in range(1, n + 1):
        q = base.copy()
        q[_IDX[joint]] += amp * math.sin(k * math.pi / 2)
        keys.append((lead + k / (4 * hz), q))
    keys.append((keys[-1][0] + hold, base.copy()))
    return KeyframePreset(name, keys)


def _hold(name: str, pose: np.ndarray, lead: float = 0.7, hold: float = 1.2) -> KeyframePreset:
    return KeyframePreset(name, [(0.0, READY.copy()), (lead, pose), (lead + hold, pose)])


# Saludo con la mano derecha: brazo hacia delante-arriba, antebrazo levantado, vaivén lateral.
_WAVE_BASE = _pose(r_pitch=-1.4, r_roll=-0.35, r_elbow=-1.4, r_wroll=0.2)
# Bienvenida VIP: brazos abiertos hacia delante, palmas hacia el visitante (sin reverencia: cintura bloqueada).
_OPEN_ARMS = _pose(l_pitch=-0.6, l_roll=0.55, l_elbow=-0.5, r_pitch=-0.6, r_roll=-0.55, r_elbow=-0.5)
# "Dame esos cinco" (staff): mano derecha arriba al frente.
_HIGH_FIVE = _pose(r_pitch=-1.9, r_roll=-0.25, r_elbow=-0.35)
# Corazón: ambas manos juntas delante del pecho (giro de hombro hacia dentro ±0.4: las manos del
# modelo oficial se tocan con ±0.5; verificado con x2_ultra.xml v1.3.0).
_HEART = _pose(l_pitch=-0.9, l_roll=0.15, l_yaw=-0.4, l_elbow=-1.6, r_pitch=-0.9, r_roll=-0.15, r_yaw=0.4,
               r_elbow=-1.6)
# Aplauso: antebrazos al frente; las manos se acercan (giro ±0.2 → ±0.6, 0.44 → 0.31 m) sin tocarse (±0.8).
_CLAP = _pose(l_pitch=-0.9, l_roll=0.25, l_yaw=-0.2, l_elbow=-1.3, r_pitch=-0.9, r_roll=-0.25, r_yaw=0.2,
              r_elbow=-1.3)
_THUMBS = _pose(r_pitch=-0.8, r_roll=-0.2, r_elbow=-1.2)
_NEG = _pose(r_pitch=-0.7, r_roll=-0.3, r_elbow=-1.3)
_CONFIRM = _pose(r_pitch=-0.6, r_roll=-0.25, r_elbow=-1.0)


def _clap() -> KeyframePreset:
    keys = [(0.0, READY.copy()), (0.6, _CLAP)]
    t = 0.6
    for _ in range(4):
        closed = _pose(_CLAP, l_yaw=-0.6, r_yaw=0.6)
        keys += [(t + 0.18, closed), (t + 0.36, _CLAP.copy())]
        t += 0.36
    keys.append((t + 0.3, _CLAP.copy()))
    return KeyframePreset("APPLAUSE", keys)


LIBRARY = {
    "GREETING_VIP": _hold("GREETING_VIP", _OPEN_ARMS, lead=0.8, hold=1.5),
    "GREETING_REGULAR": _osc("GREETING_REGULAR", _WAVE_BASE, "right_shoulder_roll_joint", 0.2, 1.3, 2.0),
    "GREETING_GUEST": _osc("GREETING_GUEST", _WAVE_BASE, "right_shoulder_roll_joint", 0.2, 1.3, 2.0),
    "GREETING_STAFF": _hold("GREETING_STAFF", _HIGH_FIVE, lead=0.8, hold=1.0),
    "GREETING_EXTERNAL": _osc("GREETING_EXTERNAL", _WAVE_BASE, "right_shoulder_roll_joint", 0.15, 1.3, 1.5),
    "FAREWELL_WAVE": _osc("FAREWELL_WAVE", _WAVE_BASE, "right_shoulder_roll_joint", 0.2, 1.3, 2.5),
    "SOCIAL_HEART": _hold("SOCIAL_HEART", _HEART, lead=0.8, hold=1.2),
    "APPLAUSE": _clap(),
    "NEGATION_NORMAL": _osc("NEGATION_NORMAL", _NEG, "right_shoulder_roll_joint", 0.12, 1.5, 1.5),
    "NEGATION_STRONG": _osc("NEGATION_STRONG", _NEG, "right_shoulder_roll_joint", 0.18, 1.8, 2.0),
    "CONFIRMATION": _hold("CONFIRMATION", _CONFIRM, lead=0.6, hold=0.8),
    "THUMBS_UP": _hold("THUMBS_UP", _THUMBS, lead=0.7, hold=1.0),
}


def _catalog_ids() -> dict[tuple[int, int], str]:
    """(motion_id, area) → nombre del emote, desde el catálogo versionado de HRIEGO."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[4] / "assets" / "hri" / "HRIEGO_gesture" / "contract" / "catalog.json"
    try:
        emotes = json.loads(root.read_text(encoding="utf-8"))["emotes"]
    except Exception:
        return {}
    return {(int(v["motion_id"]), int(v["area"])): k for k, v in emotes.items()
            if v.get("motion_id") is not None and v.get("area") is not None}


_IDS: dict[tuple[int, int], str] | None = None


def procedural_for(motion_id: int, area: int) -> KeyframePreset | None:
    global _IDS
    if _IDS is None:
        _IDS = _catalog_ids()
    name = _IDS.get((int(motion_id), int(area)))
    return LIBRARY.get(name) if name else None
