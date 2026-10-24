"""Overlay de señalamiento (POINT_LEFT/RIGHT/FRONT) sobre una base SONIC, anclado al CTC.

Diseño validado en el gemelo (``HRIEGO_gesture/twin/stream_point.py``): el stroke empieza
``LEAD_S`` antes del ancla CTC de la palabra de dirección; preparación de 0,8 s (quintic), hold de
1,5 s (≤ 2 s), recuperación de 1,65 s; el brazo que señala va de la base al objetivo de
``point_targets.json`` y la cabeza gira hacia la dirección (LEFT +, RIGHT −, FRONT 0) escalada por
el peso w. El otro brazo y el waist siguen la base.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from x2_ultra.robot import joints as J
from x2_ultra.show.hri.motion import min_jerk

PREP_S = 0.8
HOLD_S = 1.5          # ≤ 2 s por diseño
RECOV_S = 1.65
LEAD_S = 0.12
HEAD_AMP = 0.30       # rad de yaw de cabeza hacia la dirección
HEAD_SIGN = {"LEFT": 1.0, "RIGHT": -1.0, "FRONT": 0.0}
VLIM = 2.0            # rad/s: limitador de velocidad del brazo que señala


class PointOverlay:
    def __init__(self, direction: str, anchor_t: float, targets: dict):
        if direction not in HEAD_SIGN:
            raise ValueError(f"dirección de señalamiento desconocida: {direction}")
        self.direction = direction
        self.anchor_t = float(anchor_t)          # inicio del disparador en el reloj del turno
        entry = targets[direction]
        self.side = entry.get("side", "right" if direction != "LEFT" else "left")
        q = np.full(14, np.nan)
        for name, value in entry["joints"].items():
            if name in J.ARM_JOINTS:
                q[J.ARM_JOINTS.index(name)] = float(value)
        self.target = q                          # NaN en las articulaciones que no mueve
        self.parm = np.array([n.startswith(self.side) for n in J.ARM_JOINTS])
        self.ts = self.anchor_t - LEAD_S         # stroke
        self.t_end = self.ts + HOLD_S + RECOV_S

    def weight(self, t: float) -> float:
        if t < self.ts - PREP_S or t > self.t_end:
            return 0.0
        if t < self.ts:
            return min_jerk((t - (self.ts - PREP_S)) / PREP_S)
        if t <= self.ts + HOLD_S:
            return 1.0
        return 1.0 - min_jerk((t - self.ts - HOLD_S) / RECOV_S)

    def start(self) -> float:
        return self.ts - PREP_S

    def apply(self, t: float, base_arms: np.ndarray, base_head: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(arms14, head2) en el tiempo ``t`` del turno: base + w·(objetivo − base) en el brazo que
        señala; cabeza girada hacia la dirección."""
        w = self.weight(t)
        arms = base_arms.copy()
        if w > 0.0:
            tgt = np.where(self.parm & np.isfinite(self.target), self.target, arms)
            arms = arms + w * (tgt - arms)
        head = base_head.copy()
        head[0] = float(np.clip(head[0] + HEAD_SIGN[self.direction] * HEAD_AMP * w,
                                *J.JOINT_RANGE["head_yaw_joint"]))
        return arms, head
