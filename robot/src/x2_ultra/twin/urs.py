"""UPPERBODY_REMOTE_SPLIT (URS) y HEAD_ONLY emulados, sin ROS.

El MC x86 v1.0.0 no trae ninguno de los dos modos (``docs/05``), pero AimDK v1.1.0 los documenta
(``control_mod/upper_body_control``). Aquí se reproduce el contrato público:

  - ``/mc/upper_body_command`` (``UpperBodyCommandArray``) a ~50 Hz; se descartan órdenes con ``header.stamp`` a
    más de 200 ms de la hora de llegada (con stamp 0 se usa la hora de llegada);
  - en URS mandan brazos (``arm_pos``, 14) y manos (``hand_pos`` según ``hand_sub_mode``); la cabeza se ignora;
  - en HEAD_ONLY manda solo la cabeza (``head_pos``);
  - el MC sigue con el equilibrio y la marcha (en el gemelo, el MC oficial en STAND_DEFAULT).

Cómo se aplica (decisión del gemelo, no documentada por AgiBot): sustituyendo la orden HAL de brazos/cabeza que
manda el MC (que en STAND_DEFAULT deja los brazos pasivos, kp = 0) por un PD hacia el objetivo con rampa de
velocidad y compensación de gravedad, con las ganancias de brazo de ``classic/manipulate.yaml`` del propio MC.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H

URS, HEAD_ONLY = "UPPERBODY_REMOTE_SPLIT", "HEAD_ONLY"
ARM_KP, ARM_KD = 40.0, 2.0          # classic/manipulate.yaml del MC (lx2501_3_t2d5)
HEAD_KP, HEAD_KD = 10.0, 0.8        # orden de cabeza del MC en STAND_DEFAULT (eje de cabeceo)
MAX_SPEED = 2.0                     # [rad/s] rampa hacia el objetivo (evita escalones)
MAX_AGE = 0.2                       # [s]
HAND_CLAW, HAND_DEXTEROUS_JOINT, HAND_DEXTEROUS_GESTURE = 1, 2, 3
# Gestos (hand_sub_mode 3): AimDK no publica la tabla de identificadores; el gemelo usa estas poses de
# omnihand.HAND_POSES y el segundo valor de cada mano como grado de formación del gesto (0 abierta, 1 completo).
GESTURES = {0: "abierta", 1: "puño", 2: "pinza", 3: "señalar", 4: "ok", 5: "pulgar_arriba", 6: "agarre",
            7: "separar"}
_ARM_LO = np.array([J.JOINT_RANGE[n][0] for n in J.ARM_JOINTS])
_ARM_HI = np.array([J.JOINT_RANGE[n][1] for n in J.ARM_JOINTS])
_HEAD_LO = np.array([J.JOINT_RANGE[n][0] for n in J.HEAD_JOINTS])
_HEAD_HI = np.array([J.JOINT_RANGE[n][1] for n in J.HEAD_JOINTS])


def hand_targets(sub_mode: int, hand_pos) -> np.ndarray | None:
    """20 posiciones [rad] (izquierda 10 + derecha 10) para un ``hand_sub_mode``; None si no es válido."""
    v = np.asarray(hand_pos, dtype=float)
    if sub_mode == HAND_DEXTEROUS_JOINT and v.size == 20:
        return v.copy()
    if sub_mode == HAND_CLAW and v.size == 2:           # [abierto_izq, abierto_der] en 0..1 -> agarre cilíndrico
        grip = np.array(H.HAND_POSES["agarre"])
        return np.concatenate([H.to_radians("L", (1.0 - np.clip(v[0], 0, 1)) * grip),
                               H.to_radians("R", (1.0 - np.clip(v[1], 0, 1)) * grip)])
    if sub_mode == HAND_DEXTEROUS_GESTURE and v.size == 4:
        out = []
        for side, gesture, amount in (("L", v[0], v[1]), ("R", v[2], v[3])):
            pose = np.array(H.HAND_POSES[GESTURES.get(int(round(gesture)), "abierta")])
            out.append(H.to_radians(side, np.clip(amount, 0, 1) * pose))
        return np.concatenate(out)
    return None


@dataclass
class UpperBodyEmulator:
    mode: str | None = None
    enabled: bool = True
    arm_target: np.ndarray | None = None
    head_target: np.ndarray | None = None
    hand_target: np.ndarray | None = None
    _arm_cmd: np.ndarray | None = field(default=None, repr=False)
    _head_cmd: np.ndarray | None = field(default=None, repr=False)
    accepted: int = 0
    rejected: int = 0

    def set_mode(self, mode: str | None) -> None:
        if mode != self.mode:
            self.arm_target = self.head_target = self.hand_target = None
            self._arm_cmd = self._head_cmd = None
        self.mode = mode

    def on_command(self, hand_sub_mode: int, head_pos, arm_pos, hand_pos, age: float | None) -> bool:
        """Una ``UpperBodyCommandArray``. ``age``: hora de llegada − header.stamp [s] (None si stamp = 0)."""
        if not self.enabled or self.mode is None or (age is not None and abs(age) > MAX_AGE):
            self.rejected += 1
            return False
        if self.mode == URS:
            self.arm_target = np.clip(np.asarray(arm_pos, dtype=float), _ARM_LO, _ARM_HI)
            hand = hand_targets(hand_sub_mode, hand_pos)
            if hand is not None:
                self.hand_target = hand
        elif self.mode == HEAD_ONLY:
            self.head_target = np.clip(np.asarray(head_pos, dtype=float), _HEAD_LO, _HEAD_HI)
        self.accepted += 1
        return True

    @staticmethod
    def _ramp(cmd: np.ndarray, target: np.ndarray, dt: float) -> np.ndarray:
        step = MAX_SPEED * dt
        return cmd + np.clip(target - cmd, -step, step)

    def apply(self, phys) -> None:
        """Antes de cada paso de física: sustituye la orden HAL de brazos (URS) o de cabeza (HEAD_ONLY)."""
        if self.mode is None:
            return
        dt = phys.model.opt.timestep
        d = phys.data
        if self.mode == URS and self.arm_target is not None:
            if self._arm_cmd is None:
                self._arm_cmd = d.qpos[phys.qadr["arm"]].copy()
            self._arm_cmd = self._ramp(self._arm_cmd, self.arm_target, dt)
            n = len(J.ARM_JOINTS)
            phys.set_joint_command("arm", self._arm_cmd, np.zeros(n), d.qfrc_bias[phys.dadr["arm"]],
                                   np.full(n, ARM_KP), np.full(n, ARM_KD))
        if self.mode == URS and self.hand_target is not None:
            phys.set_hand_targets(self.hand_target)
        if self.mode == HEAD_ONLY and self.head_target is not None:
            if self._head_cmd is None:
                self._head_cmd = d.qpos[phys.qadr["head"]].copy()
            self._head_cmd = self._ramp(self._head_cmd, self.head_target, dt)
            n = len(J.HEAD_JOINTS)
            phys.set_joint_command("head", self._head_cmd, np.zeros(n), d.qfrc_bias[phys.dadr["head"]],
                                   np.full(n, HEAD_KP), np.full(n, HEAD_KD))
