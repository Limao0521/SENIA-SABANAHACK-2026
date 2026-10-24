"""Lectura y reproducción cinemática de los JSON de movimiento de LinkCraft."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

from x2_ultra.robot import joints as J

LITE_ABSENT_JOINTS = {
    "left_wrist_pitch_joint", "left_wrist_roll_joint",
    "right_wrist_pitch_joint", "right_wrist_roll_joint",
}


@dataclass(frozen=True)
class LinkCraftClip:
    fps: float
    position: np.ndarray
    rotation: np.ndarray
    joints: np.ndarray

    @property
    def duration(self) -> float:
        return (len(self.joints) - 1) / self.fps

    @classmethod
    def load(cls, path: Path) -> "LinkCraftClip":
        raw = json.loads(Path(path).read_text())
        frames = raw["frames"]
        fps = float(raw["fps"])
        if len(frames) < 2 or not math.isfinite(fps) or fps <= 0:
            raise ValueError("El movimiento necesita al menos dos cuadros y fps positivo")
        expected = set(J.BODY_JOINTS)
        accepted = (expected, expected - LITE_ABSENT_JOINTS)
        joint_set = set(frames[0]["joints"])
        if joint_set not in accepted:
            raise ValueError("El movimiento no corresponde a X2 Ultra o X2 Lite")
        for i, frame in enumerate(frames):
            if set(frame["joints"]) != joint_set:
                raise ValueError(f"Cuadro {i}: cambió el conjunto de articulaciones")
        position = np.asarray([f["rootPosition"] for f in frames], dtype=float)
        rotation = np.asarray([f["rootRotation"] for f in frames], dtype=float)
        joints = np.asarray([[f["joints"].get(n, J.DEFAULT_POSE[n]) for n in J.BODY_JOINTS]
                             for f in frames], dtype=float)
        if position.shape != (len(frames), 3) or rotation.shape != position.shape or not all(
            np.isfinite(a).all() for a in (position, rotation, joints)
        ):
            raise ValueError("El movimiento tiene dimensiones incorrectas o valores no finitos")
        return cls(fps, position, rotation, joints)

    def sample(self, seconds: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        frame = np.clip(seconds * self.fps, 0, len(self.joints) - 1)
        i = int(frame)
        j = min(i + 1, len(self.joints) - 1)
        u = frame - i
        blend = lambda a: a[i] * (1 - u) + a[j] * u
        return blend(self.position), blend(self.rotation), blend(self.joints)

    def sample_mujoco(self, seconds: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Base (posición, cuaternión wxyz) en ejes MuJoCo y las 31 articulaciones (orden ``BODY_JOINTS``)."""
        pos, rot, joints = self.sample(seconds)
        return to_mujoco_position(pos), _rpy_quat(rot), joints


def to_mujoco_position(pos: np.ndarray) -> np.ndarray:
    """rootPosition de LinkCraft (Y arriba, Z a la derecha del robot) a MuJoCo (Z arriba, Y a la izquierda).

    Comprobado con los pies: con (x, −z, y) los pies apoyados deslizan 3-5 mm por cuadro en los cinco clips
    descargados; con (x, z, y), que refleja el desplazamiento lateral, 18-25 mm."""
    return np.array([pos[0], -pos[2], pos[1]])


def _rpy_quat(angles: np.ndarray) -> np.ndarray:
    """Euler roll/pitch/yaw (rad) a cuaternión MuJoCo wxyz."""
    r, p, y = angles / 2
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    return np.array([cr * cp * cy + sr * sp * sy,
                     sr * cp * cy - cr * sp * sy,
                     cr * sp * cy + sr * cp * sy,
                     cr * cp * sy - sr * sp * cy])


class LinkCraftPlayer:
    """Coloca el modelo en cada cuadro sin usar la policy de marcha ni integrar física.

    LinkCraft usa Y vertical en rootPosition; MuJoCo usa Z vertical (``to_mujoco_position``). rootRotation es
    roll/pitch/yaw sobre los ejes de MuJoCo (el JSON no lo documenta; se comprobó con el deslizamiento de los pies).
    """

    def __init__(self, model: mujoco.MjModel, clip: LinkCraftClip):
        self.model, self.clip = model, clip
        jid = lambda n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n)
        base = jid("floating_base_joint")
        if base < 0:
            raise ValueError("El modelo no tiene floating_base_joint")
        self.base_adr = model.jnt_qposadr[base]
        ids = [jid(n) for n in J.BODY_JOINTS]
        if any(i < 0 for i in ids):
            raise ValueError("Faltan articulaciones X2 en el modelo")
        self.joint_adr = model.jnt_qposadr[ids]

    def pose(self, data: mujoco.MjData, seconds: float) -> None:
        pos, quat, joints = self.clip.sample_mujoco(seconds)
        q = data.qpos
        a = self.base_adr
        q[a:a + 3] = pos
        q[a + 3:a + 7] = quat
        q[self.joint_adr] = joints
        data.qvel[:] = 0
        data.time = min(max(seconds, 0.0), self.clip.duration)
        mujoco.mj_forward(self.model, data)
