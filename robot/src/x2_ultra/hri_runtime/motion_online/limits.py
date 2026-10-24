"""Límites de seguridad y post-proceso del stream de movimiento online (T4).

Fuentes (todo probado en Reto 2 / HRIEGO):
- ``HRIEGO_gesture/motion/prep_arms.py``: recortes de posición por articulación (URDF v1.4.0
  con margen 0.05 rad, ``LIMIT_MARGIN`` de control.py).
- Commit 65de416 (reto2): ``SONIC_ARM_AMP_K = 0.7`` alrededor del reposo y
  ``WAIST_BOUNDS = [0.10, 0.05, 0.03]`` rad (yaw/pitch/roll).
- ``docs/reto-2/HRI_RUNTIME_SPEC.md`` §seguridad: 10 rad/s por articulación fuera de emotes;
  aceleración 40 rad/s² (p99.9 de SONIC, usado por el limiter del motor en opt/o3_brain_motor).

Solo numpy: este módulo lo usa tanto el cliente (show engine) como los tests.
"""
from __future__ import annotations

import numpy as np

# --------------------------------------------------------------------------- posiciones
SIDES = ("left", "right")
PARTS = ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow",
         "wrist_yaw", "wrist_pitch", "wrist_roll")
ARM_JOINTS = [f"{s}_{p}_joint" for s in SIDES for p in PARTS]
WAIST_JOINTS = ["waist_yaw_joint", "waist_pitch_joint", "waist_roll_joint"]
HEAD_JOINTS = ["head_yaw_joint", "head_pitch_joint"]

_R = {"shoulder_pitch": (-3.0805, 2.0333), "shoulder_yaw": (-2.5569, 2.5569),
      "elbow": (-2.3562, 0.0), "wrist_yaw": (-2.5569, 2.5569),
      "wrist_pitch": (-0.5236, 0.5236)}
_LIM = {}
for _s in SIDES:
    for _p in PARTS:
        if _p == "shoulder_roll":
            _lo, _hi = (-0.0611, 3.0456) if _s == "left" else (-3.0456, 0.0611)
        elif _p == "wrist_roll":
            _lo, _hi = (-1.5097, 0.7243) if _s == "left" else (-0.7243, 1.5097)
        else:
            _lo, _hi = _R[_p]
        _LIM[f"{_s}_{_p}_joint"] = (_lo + 0.05, _hi - 0.05)     # LIMIT_MARGIN de prep_arms

ARM_LO = np.array([_LIM[n][0] for n in ARM_JOINTS])
ARM_HI = np.array([_LIM[n][1] for n in ARM_JOINTS])

# Reposo del show (misma fuente que player._base del Reto 2) y amplitud SONIC k=0.7.
try:                                                                    # pragma: no cover
    from x2_ultra.show.hri.motion import REST_ARMS, SONIC_ARM_AMP_K
except Exception:                                                       # entorno sin paquete: fallback
    SONIC_ARM_AMP_K = 0.7
    from x2_ultra.robot.joints import DEFAULT_POSE as _DP               # type: ignore
    REST_ARMS = np.array([_DP[n] for n in ARM_JOINTS])

# Waist: bounds del canal SONIC (commit 65de416, legs2 c_b10k07). El show además lo filtra
# (WaistFilter); aquí solo recortamos para que el stream IPC ya viaje acotado.
WAIST_BOUNDS_ONLINE = np.array([0.10, 0.05, 0.03])
HEAD_LIMIT = np.array([2.0, 0.7])          # rad |head_yaw|, |head_pitch| (URDF v1.4.0, holgado)

# --------------------------------------------------------------------------- velocidad/aceleración
VEL_LIMIT = 10.0           # rad/s por articulación, fuera de emotes (HRI_RUNTIME_SPEC)
ACC_LIMIT = 40.0           # rad/s² (p99.9 de SONIC; mismo amax del limiter del motor o3)
DT = 0.02                  # 50 Hz

# Límites por articulación de la spec (rad/s): emotes por grupo, resto 10.
SPEC_VEL_EMOTE = {"hip_roll": 40.0, "hip_pitch": 40.0, "hip_yaw": 30.0,
                  "waist": 20.0, "arm": 20.0, "head": 30.0}


class JointLimiter:
    """Limitador causal por articulación: velocidad ≤ vmax y aceleración ≤ amax (50 Hz).

    Seguidor simple: la velocidad de salida cambia a lo sumo ``amax*dt`` por tick y está
    acotada por ``vmax``; la posición de salida integra esa velocidad. Sin estado compartido:
    un objeto por stream (brazos+waist+cabeza se limitan con sus propios vmax/amax).
    """

    def __init__(self, n: int, q0: np.ndarray | None = None, vmax: float = VEL_LIMIT,
                 amax: float = ACC_LIMIT, dt: float = DT):
        self.n, self.vmax, self.amax, self.dt = int(n), float(vmax), float(amax), float(dt)
        self.q = np.zeros(self.n) if q0 is None else np.asarray(q0, float).reshape(-1)[:self.n].copy()
        self.v = np.zeros(self.n)

    def reset(self, q0: np.ndarray | None = None) -> None:
        self.q = np.zeros(self.n) if q0 is None else np.asarray(q0, float).reshape(-1)[:self.n].copy()
        self.v = np.zeros(self.n)

    def step(self, q_target: np.ndarray) -> np.ndarray:
        """Un tick: sigue el objetivo con |v| ≤ vmax, |a| ≤ amax, sin rebasarlo (perfil de frenado).

        ``v_brake(d)`` es la raíz de v²/(2a) + v·dt = d: cota superior exacta de la distancia de
        parada discreta a deceleración ``amax`` (D(v) ≤ v·dt + v²/(2a)), de modo que con
        v ≤ v_brake(d) el paso nunca pasa de largo (sin overshoot) y |a| se mantiene ≤ amax.
        """
        tq = np.asarray(q_target, float).reshape(-1)[:self.n]
        dist = tq - self.q
        a, dt = self.amax, self.dt
        v_brake = -a * dt + np.sqrt((a * dt) ** 2 + 2.0 * a * np.abs(dist))
        v_des = np.sign(dist) * np.minimum(np.abs(dist) / dt, v_brake)
        dv = a * dt
        self.v = np.clip(self.v + np.clip(v_des - self.v, -dv, dv), -self.vmax, self.vmax)
        self.q = self.q + self.v * dt
        return self.q.copy()


def postprocess(arms14: np.ndarray, waist3: np.ndarray, head2: np.ndarray,
                k: float = SONIC_ARM_AMP_K) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Recortes estáticos de un frame: brazos (posición + amplitud k), waist (bounds), cabeza."""
    arms = np.clip(np.asarray(arms14, float).reshape(14), ARM_LO, ARM_HI)
    arms = REST_ARMS + k * (arms - REST_ARMS)
    waist = np.clip(np.asarray(waist3, float).reshape(3), -WAIST_BOUNDS_ONLINE, WAIST_BOUNDS_ONLINE)
    head = np.clip(np.asarray(head2, float).reshape(2), -HEAD_LIMIT, HEAD_LIMIT)
    return arms, waist, head
