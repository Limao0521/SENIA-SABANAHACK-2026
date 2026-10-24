"""Bailes de LinkCraft en el escenario: un robot reproduce un clip cuadro a cuadro mientras el otro sigue con física.

La reproducción es **cinemática**: se impone la postura del clip (base y articulaciones) en cada paso de física, sin
policy ni comprobación de equilibrio; muestra la coreografía, no si el robot real la aguanta (en el robot la ejecuta
el MC). El clip se coloca donde está el robot al empezar: su primer cuadro se lleva a la posición y el rumbo actuales,
y la altura se mide desde la tarima.

Fases: 0,6 s de transición desde la postura actual al primer cuadro, el clip, y 0,6 s hasta quedar de pie (postura
por defecto, con la posición y el rumbo del último cuadro). Entonces el robot vuelve a la policy de marcha.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import mujoco
import numpy as np

from x2_ultra import paths
from x2_ultra.robot import joints as J
from x2_ultra.sim.engine import _yaw
from x2_ultra.tools.linkcraft_dance import LinkCraftClip

MOTIONS_DIR = paths.ASSETS_DIR / "motions"
BLEND = 0.6            # s, transiciones de entrada y salida
LABELS = {"tai_chi_1": "Tai Chi (1)", "chen_style_tai_chi": "Tai Chi estilo Chen", "tai_chi_2": "Tai Chi (2)",
          "kung_fu": "Kung Fu", "linkcraft_x2_ultra_beat_dance": "Beat dance", "beat_dance": "Beat dance"}


def available_dances(directory: Path = MOTIONS_DIR) -> dict[str, Path]:
    """Clips JSON de LinkCraft disponibles: {nombre visible: ruta}."""
    if not directory.is_dir():
        return {}
    return {LABELS.get(p.stem, p.stem.replace("_", " ").capitalize()): p for p in sorted(directory.glob("*.json"))}


@lru_cache(maxsize=8)
def load_clip(path: Path) -> LinkCraftClip:
    return LinkCraftClip.load(path)


def _yaw_quat(yaw: float) -> np.ndarray:
    return np.array([math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)])


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def _nlerp(a: np.ndarray, b: np.ndarray, u: float) -> np.ndarray:
    if np.dot(a, b) < 0:
        b = -b
    q = (1 - u) * a + u * b
    return q / np.linalg.norm(q)


def _smooth(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


@dataclass
class Pose:
    pos: np.ndarray
    quat: np.ndarray
    joints: np.ndarray        # orden de DanceTrack.joint_names

    def blend(self, other: "Pose", u: float) -> "Pose":
        return Pose((1 - u) * self.pos + u * other.pos, _nlerp(self.quat, other.quat, u),
                    (1 - u) * self.joints + u * other.joints)


class DanceTrack:
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, prefix: str, joint_names, clip: LinkCraftClip,
                 name: str, t0: float, floor_z: float, stand_height: float):
        self.clip, self.name, self.t0 = clip, name, t0
        self.floor_z, self.stand_height = floor_z, stand_height
        jid = lambda n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, prefix + n)
        base = jid("floating_base_joint")
        self.qbase, self.vbase = model.jnt_qposadr[base], model.jnt_dofadr[base]
        self.joint_names = tuple(joint_names)
        self.qadr = model.jnt_qposadr[[jid(n) for n in self.joint_names]]
        self.vadr = model.jnt_dofadr[[jid(n) for n in self.joint_names]]
        self._clip_cols = np.array([J.BODY_JOINTS.index(n) for n in self.joint_names])
        self.start = self.current(data)
        # Anclaje: el primer cuadro del clip queda donde está el robot, con su rumbo.
        p0, q0, _ = clip.sample_mujoco(0.0)
        yaw = _yaw(self.start.quat)
        self._yaw = yaw
        self._rot = np.array([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
        self._offset = self.start.pos[:2] - self._rot @ p0[:2]
        self.stopped_at: float | None = None
        self._stop_from: Pose | None = None
        self.end_pose: Pose | None = None

    # ------------------------------------------------------------------ poses
    def current(self, data: mujoco.MjData) -> Pose:
        a = self.qbase
        return Pose(data.qpos[a:a + 3].copy(), data.qpos[a + 3:a + 7].copy(), data.qpos[self.qadr].copy())

    def clip_pose(self, seconds: float) -> Pose:
        pos, quat, joints = self.clip.sample_mujoco(seconds)
        xy = self._rot @ pos[:2] + self._offset
        return Pose(np.array([xy[0], xy[1], pos[2] + self.floor_z]), _qmul(_yaw_quat(self._yaw), quat),
                    joints[self._clip_cols])

    def _stand(self, last: Pose) -> Pose:
        joints = np.array([J.DEFAULT_POSE[n] for n in self.joint_names])
        return Pose(np.array([last.pos[0], last.pos[1], self.stand_height]), _yaw_quat(_yaw(last.quat)), joints)

    @property
    def duration(self) -> float:
        """Duración total con las transiciones (o hasta el final de la salida si se paró antes)."""
        if self.stopped_at is not None:
            return self.stopped_at - self.t0 + BLEND
        return self.clip.duration + 2 * BLEND

    def clip_time(self, t: float) -> float:
        return min(max(t - self.t0 - BLEND, 0.0), self.clip.duration)

    def pose(self, t: float) -> Pose:
        s = t - self.t0
        if self.stopped_at is not None:
            if self.end_pose is None:
                self.end_pose = self._stand(self._stop_from)
            return self._stop_from.blend(self.end_pose, _smooth((t - self.stopped_at) / BLEND))
        if s < BLEND:
            return self.start.blend(self.clip_pose(0.0), _smooth(s / BLEND))
        if s <= BLEND + self.clip.duration:
            return self.clip_pose(s - BLEND)
        last = self.clip_pose(self.clip.duration)
        if self.end_pose is None:
            self.end_pose = self._stand(last)
        return last.blend(self.end_pose, _smooth((s - BLEND - self.clip.duration) / BLEND))

    def done(self, t: float) -> bool:
        return t - self.t0 >= self.duration

    def stop(self, t: float) -> None:
        """Termina ya: transición desde la postura actual hasta quedar de pie."""
        if self.stopped_at is None:
            self._stop_from = self.pose(t)
            self.end_pose = None
            self.stopped_at = t

    # ------------------------------------------------------------------ aplicar
    def apply(self, data: mujoco.MjData, t: float) -> None:
        p = self.pose(t)
        a, v = self.qbase, self.vbase
        data.qpos[a:a + 3] = p.pos
        data.qpos[a + 3:a + 7] = p.quat
        data.qpos[self.qadr] = p.joints
        data.qvel[v:v + 6] = 0.0
        data.qvel[self.vadr] = 0.0
