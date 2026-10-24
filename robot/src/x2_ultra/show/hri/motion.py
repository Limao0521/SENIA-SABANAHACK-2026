"""Reproducción cinemática de movimiento precomputado: presets AgiBot (CSV) y clips SONIC (npz).

Presets (``CsvPreset``): los CSV de ``mc_param/.../classic/animation`` traen ``timeMS`` y columnas
``command_pos::<articulación>`` (waist 3, brazos 14 en el orden SDK L7+R7, cabeza 2, manos que aquí
se ignoran). El cuadro 0 es la postura por defecto del planificador D (shoulder_pitch 0,4, elbow
−1,2, lo demás 0): la reproducción empieza y termina en D, así que las mezclas con SONIC no saltan.

Clips SONIC (``SonicClip``): npz a 50 Hz con ``cmd`` (14 brazos), ``waist_head`` (5) y ``t``; el
cuadro 100 (t = 2,0 s) es el t0 del audio, los 0-99 son la entrada (READY). El waist de SONIC y de
los CSV se usa como *delta acotado* sobre la policy de marcha (solo con el robot parado).

Mezclas minimum-jerk (patrón V2 validado en c12): SONIC → desvanecer 0,6 s a D → preset →
desvanecer 0,7 s de vuelta a SONIC (si queda audio) o al reposo del show.
"""
from __future__ import annotations

import csv as _csv
from pathlib import Path

import numpy as np

from x2_ultra.robot import joints as J

DT = 0.02                 # 50 Hz, como los streams del gemelo
FRAME_T0 = 100            # cuadro del npz SONIC que corresponde al t0 del audio
AUDIO_LEADIN = FRAME_T0 * DT      # 2,0 s de entrada antes del audio
FADE_OUT_S = 0.6          # SONIC → D antes del preset (c12 V2)
FADE_IN_S = 0.7           # preset → SONIC/reposo después del preset (c12 V2)
BLEND_S = 0.3             # mezcla entre turnos
TRACK_LAG_S = 0.10        # retardo medido del PD de brazos en el cruce del 90 % (validación E2):
                          # el stroke *medido* cae ~80-200 ms (mediana 100) después del cinemático;
                          # se compensa adelantando el dispatch (feedforward, como las latencias c08)
WAIST_LIMIT = np.array([0.35, 0.15, 0.15])     # rad: delta acotado sobre la policy (parado)

# Postura por defecto del planificador (D), primer cuadro de los CSV; y el READY de SONIC.
D_ARMS = np.array([0.4, 0.0, 0.0, -1.2, 0.0, 0.0, 0.0, 0.4, 0.0, 0.0, -1.2, 0.0, 0.0, 0.0])
READY_ARMS = np.array([0.2, 0.2, 0.0, -0.6, 0.0, 0.0, 0.0, 0.2, -0.2, 0.0, -0.6, 0.0, 0.0, 0.0])
REST_ARMS = np.array([J.DEFAULT_POSE[n] for n in J.ARM_JOINTS])     # reposo del show (guide.py)
SONIC_ARM_AMP_K = 0.7   # escalado de amplitud de brazos SONIC alrededor del reposo:
                        # cmd = REST + k·(cmd − REST). Fix legs2 c_b10k07 (out_diag/legs2/REPORT.md):
                        # baja las reacciones de piernas sin perder gestos visibles (arm RMS 70 %).
                        # NO se aplica a presets cinemáticos (CSV) ni a pointing.

_ARM_COLS = [f"command_pos::{n}" for n in J.ARM_JOINTS]
_WAIST_COLS = [f"command_pos::{n}" for n in J.WAIST_JOINTS]
_HEAD_COLS = [f"command_pos::{n}" for n in J.HEAD_JOINTS]


def min_jerk(w: float) -> float:
    """Perfil minimum-jerk 0→1 (quintic): velocidad y aceleración nulas en los extremos."""
    x = float(np.clip(w, 0.0, 1.0))
    return x * x * x * (10.0 + x * (-15.0 + 6.0 * x))


def fade(t: float, t0: float, dur: float) -> float:
    return min_jerk((t - t0) / dur) if dur > 0 else (1.0 if t >= t0 else 0.0)


class CsvPreset:
    """Animación AgiBot de un CSV: muestreo lineal de waist/brazos/cabeza en tiempo de preset."""

    _cache: dict[Path, "CsvPreset"] = {}

    def __init__(self, path: Path):
        self.path = Path(path)
        times, rows = [], []
        with self.path.open(newline="") as fh:
            reader = _csv.DictReader(fh)
            fields = reader.fieldnames or []
            arm_cols = [c for c in _ARM_COLS if c in fields]
            waist_cols = [c for c in _WAIST_COLS if c in fields]
            head_cols = [c for c in _HEAD_COLS if c in fields]
            if len(arm_cols) != 14:
                raise ValueError(f"CSV sin las 14 columnas de brazos: {self.path}")
            cols = arm_cols + waist_cols + head_cols
            for row in reader:
                times.append(float(row["timeMS"]) / 1000.0)
                rows.append([float(row[c]) for c in cols])
        self.t = np.asarray(times)
        self.q = np.asarray(rows)                      # (N, 14 + nw + nh)
        self.n_waist, self.n_head = len(waist_cols), len(head_cols)
        self.duration = float(self.t[-1])

    @classmethod
    def load(cls, path: Path) -> "CsvPreset":
        path = Path(path)
        if path not in cls._cache:
            cls._cache[path] = cls(path)
        return cls._cache[path]

    def sample(self, t: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(arms14, waist3, head2) en el tiempo ``t`` del preset (fuera de rango: extremos)."""
        tt = float(np.clip(t, self.t[0], self.t[-1]))
        q = np.empty(self.q.shape[1])
        for i in range(self.q.shape[1]):
            q[i] = np.interp(tt, self.t, self.q[:, i])
        nw = self.n_waist
        return q[:14], q[14:14 + nw], q[14 + nw:14 + nw + self.n_head]


class SonicClip:
    """Brazos + waist/head SONIC de un turno (npz de visits_v2/<rank>/sonic)."""

    _cache: dict[Path, "SonicClip"] = {}

    def __init__(self, path: Path):
        z = np.load(path)
        names = [str(n) for n in z["names"]] if "names" in z.files else list(J.ARM_JOINTS)
        if names != list(J.ARM_JOINTS):                # reordenar si hiciera falta
            order = [names.index(n) for n in J.ARM_JOINTS]
            z_cmd = np.asarray(z["cmd"])[:, order]
        else:
            z_cmd = np.asarray(z["cmd"])
        self.t = np.asarray(z["t"], dtype=float)
        self.cmd = z_cmd                                # (N, 14) brazos
        wh = np.asarray(z["waist_head"])                # (N, 5): waist 3 + head 2
        wh_names = [str(n) for n in z["waist_head_names"]] if "waist_head_names" in z.files else \
            list(J.WAIST_JOINTS) + list(J.HEAD_JOINTS)
        order = [wh_names.index(n) for n in list(J.WAIST_JOINTS) + list(J.HEAD_JOINTS)
                 if n in wh_names]
        self.waist_head = wh[:, order] if len(order) == wh.shape[1] else wh
        self.duration = float(self.t[-1])

    @classmethod
    def load(cls, path: Path) -> "SonicClip":
        path = Path(path)
        if path not in cls._cache:
            cls._cache[path] = cls(path)
        return cls._cache[path]

    def _interp(self, arr: np.ndarray, t: float) -> np.ndarray:
        tt = float(np.clip(t, self.t[0], self.t[-1]))
        return np.array([np.interp(tt, self.t, arr[:, i]) for i in range(arr.shape[1])])

    def arms(self, t: float) -> np.ndarray:
        return self._interp(self.cmd, t)

    def waist(self, t: float) -> np.ndarray:
        return np.clip(self._interp(self.waist_head, t)[:3], -WAIST_LIMIT, WAIST_LIMIT)

    def head(self, t: float) -> np.ndarray:
        return self._interp(self.waist_head, t)[3:5]
