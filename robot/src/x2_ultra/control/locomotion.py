"""Controladores de marcha (policies ONNX) que corren dentro del simulador a 50 Hz.

- ``ArmsFreePolicy``: la nuestra (``x2_arms_free_walk.onnx``). Mueve piernas y cintura; brazos, cabeza y
  manos quedan libres para el usuario. Contrato en ``x2_ultra.control.obs_spec``.
- ``AgibotPolicy``: la oficial de AgiBot (mc-rl ``policy.onnx``, Apache-2.0). Mueve las 29 articulaciones
  (incluidos los brazos), así que con ella el usuario solo camina. Se incluye para comparar.
"""
from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from x2_ultra import paths
from x2_ultra.control import obs_spec as S
from x2_ultra.robot import joints as J


def _session(onnx_path: Path):
    """Sesión ONNX en CPU. onnxruntime se importa aquí: quien no usa policies (p. ej. la app sobre el gemelo
    digital, en el contenedor) no lo necesita."""
    import onnxruntime as ort
    return ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])


class _RobotState:
    """Lee el estado de un robot del modelo por nombre (con ``prefix`` si hay varios robots en la escena, p. ej.
    ``"edu/"``). Las articulaciones que el robot no tiene (la cabeza del X2 EDU) se leen en su postura por defecto
    y con velocidad cero."""

    def __init__(self, model: mujoco.MjModel, joints, prefix: str = ""):
        jid = lambda n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, prefix + n)
        ids = np.array([jid(n) for n in joints])
        base = jid("floating_base_joint")
        if base < 0:
            raise KeyError(f"no hay base flotante {prefix}floating_base_joint en el modelo")
        self.qbase, self.vbase = model.jnt_qposadr[base], model.jnt_dofadr[base]
        self.present = ids >= 0
        self.qadr = model.jnt_qposadr[ids[self.present]]
        self.vadr = model.jnt_dofadr[ids[self.present]]
        self.default = np.array([J.DEFAULT_POSE[n] for n in joints])

    def ang_vel(self, data: mujoco.MjData) -> np.ndarray:
        return data.qvel[self.vbase + 3:self.vbase + 6]

    def quat(self, data: mujoco.MjData) -> np.ndarray:
        return data.qpos[self.qbase + 3:self.qbase + 7]

    def joint_pos_rel(self, data: mujoco.MjData) -> np.ndarray:
        q = self.default.copy()
        q[self.present] = data.qpos[self.qadr]
        return q - self.default

    def joint_vel(self, data: mujoco.MjData) -> np.ndarray:
        v = np.zeros(len(self.default))
        v[self.present] = data.qvel[self.vadr]
        return v


def _projected_gravity(quat: np.ndarray) -> np.ndarray:
    """R(quat)^T · (0, 0, -1) con quat = (w, x, y, z)."""
    w, x, y, z = quat
    return np.array([-2 * (x * z - w * y), -2 * (y * z + w * x), -(1 - 2 * (x * x + y * y))])


class _History:
    """Historial por término (más antiguo primero). Tras ``reset`` se rellena con la primera muestra."""

    def __init__(self, terms, length):
        self.terms, self.length = terms, length
        self.buf = {name: np.zeros((length, dim)) for name, dim in terms}
        self.fresh = True

    def reset(self):
        self.fresh = True

    def push(self, frame: dict) -> np.ndarray:
        for name, _ in self.terms:
            b = self.buf[name]
            if self.fresh:
                b[:] = frame[name]
            else:
                b[:-1] = b[1:]
                b[-1] = frame[name]
        self.fresh = False
        return np.concatenate([self.buf[name].reshape(-1) for name, _ in self.terms])


def _mirror_maps(joints) -> tuple[np.ndarray, np.ndarray]:
    """Espejo izquierda↔derecha en espacio articular: ``q_espejo = signo * q[perm]``.

    Los ejes del X2 son iguales en ambos lados (pitch = Y, roll = X, yaw = Z), así que al reflejar en el plano
    sagital se intercambian los lados, el pitch (y rodilla/codo) conserva el signo y roll/yaw lo cambian.
    """
    joints = list(joints)
    perm = np.array([joints.index(J.mirror_name(n)) for n in joints])
    sign = np.array([-1.0 if ("_roll_" in n or "_yaw_" in n) else 1.0 for n in joints])
    return perm, sign


class ArmsFreePolicy:
    """Policy propia. Si su metadato ``preferred_turn`` vale ±1, la policy gira bien en el sitio solo hacia ese
    lado (+1 = izquierda, wz > 0): para el otro se evalúa reflejada (observación espejo → policy → acción espejo).
    El robot es simétrico, así que la policy reflejada es tan válida como la original; el historial está siempre
    en el marco real y se refleja entero al cambiar, por eso el cambio no tiene saltos de estado."""

    name = "brazos_libres"
    controls_arms = False
    MIRROR_HYST = 0.1   # [rad/s] banda muerta del cambio de lado (evita alternar con correcciones pequeñas de rumbo)

    def __init__(self, model: mujoco.MjModel, onnx_path: Path | None = None, prefix: str = ""):
        onnx_path = Path(onnx_path or paths.default_policy())
        if not onnx_path.exists():
            raise FileNotFoundError(f"No existe la policy {onnx_path}. Entrénala con `python -m x2_ultra.rl.train`"
                                    " o usa --policy agibot.")
        meta_path = onnx_path.with_suffix(".json")
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta["obs_dim"] != S.OBS_DIM or meta["action_joints"] != list(S.ACTION_JOINTS):
                raise ValueError(f"{onnx_path} no coincide con el contrato de obs_spec.py")
        self.preferred_turn = int(meta.get("preferred_turn", 0))
        self.mirrored = False
        self._obs_perm, self._obs_sign = _mirror_maps(S.OBS_JOINTS)
        self._act_perm, self._act_sign = _mirror_maps(S.ACTION_JOINTS)
        self.session = _session(onnx_path)
        self.input_name = self.session.get_inputs()[0].name
        self.state = _RobotState(model, S.OBS_JOINTS, prefix)
        self.default_act = np.array([J.DEFAULT_POSE[n] for n in S.ACTION_JOINTS])
        self.joints = S.ACTION_JOINTS
        self.hist = _History(S.OBS_TERMS, S.HISTORY)
        self.last_action = np.zeros(S.NUM_ACTIONS)
        self.path = onnx_path

    def reset(self) -> None:
        self.hist.reset()
        self.last_action[:] = 0.0
        self.mirrored = False

    def _update_side(self, wz: float) -> None:
        """Refleja la policy mientras se pida girar hacia el lado que no domina (con histéresis)."""
        if not self.preferred_turn:
            return
        s = wz * self.preferred_turn
        if s < -self.MIRROR_HYST:
            self.mirrored = True
        elif s > self.MIRROR_HYST:
            self.mirrored = False

    def _mirror_history(self) -> np.ndarray:
        """Observación completa (historial) reflejada; ``self.hist.buf`` tiene filas = pasos, columnas = término."""
        flip_vec = {"base_ang_vel": np.array([-1.0, 1.0, -1.0]), "projected_gravity": np.array([1.0, -1.0, 1.0]),
                    "velocity_commands": np.array([1.0, -1.0, -1.0])}
        parts = []
        for name, _ in S.OBS_TERMS:
            b = self.hist.buf[name]
            if name in flip_vec:
                parts.append(b * flip_vec[name])
            elif name == "last_action":
                parts.append(b[:, self._act_perm] * self._act_sign)
            else:
                parts.append(b[:, self._obs_perm] * self._obs_sign)
        return np.concatenate([p.reshape(-1) for p in parts])

    def act(self, data: mujoco.MjData, cmd: np.ndarray) -> np.ndarray:
        """Objetivos [rad] para ``self.joints`` (piernas + cintura)."""
        cmd = np.asarray(cmd, dtype=float)
        st = self.state
        frame = {
            "base_ang_vel": st.ang_vel(data) * S.ANG_VEL_SCALE,
            "projected_gravity": _projected_gravity(st.quat(data)),
            "velocity_commands": cmd,
            "joint_pos_rel": st.joint_pos_rel(data),
            "joint_vel_rel": st.joint_vel(data) * S.JOINT_VEL_SCALE,
            "last_action": self.last_action,
        }
        obs = self.hist.push(frame)
        self._update_side(cmd[2])
        if self.mirrored:
            obs = self._mirror_history()
        obs = np.clip(obs, -S.OBS_CLIP, S.OBS_CLIP).astype(np.float32)[None]
        action = self.session.run(None, {self.input_name: obs})[0][0].astype(float)
        action = np.clip(action, -S.ACTION_CLIP, S.ACTION_CLIP)
        if self.mirrored:
            action = action[self._act_perm] * self._act_sign
        self.last_action = action
        return self.default_act + S.ACTION_SCALE * action


# Orden de observación/acción de la policy oficial (Isaac Lab intercala izquierda/derecha).
AGIBOT_OBS_JOINTS = (
    "left_hip_pitch_joint", "right_hip_pitch_joint", "waist_yaw_joint", "left_hip_roll_joint",
    "right_hip_roll_joint", "waist_pitch_joint", "left_hip_yaw_joint", "right_hip_yaw_joint", "waist_roll_joint",
    "left_knee_joint", "right_knee_joint", "head_yaw_joint", "left_shoulder_pitch_joint",
    "right_shoulder_pitch_joint", "left_ankle_pitch_joint", "right_ankle_pitch_joint", "head_pitch_joint",
    "left_shoulder_roll_joint", "right_shoulder_roll_joint", "left_ankle_roll_joint", "right_ankle_roll_joint",
    "left_shoulder_yaw_joint", "right_shoulder_yaw_joint", "left_elbow_joint", "right_elbow_joint",
    "left_wrist_yaw_joint", "right_wrist_yaw_joint", "left_wrist_pitch_joint", "right_wrist_pitch_joint",
    "left_wrist_roll_joint", "right_wrist_roll_joint",
)
AGIBOT_ACTION_JOINTS = tuple(n for n in AGIBOT_OBS_JOINTS if not n.startswith("head_"))
AGIBOT_TERMS = (("base_ang_vel", 3), ("projected_gravity", 3), ("velocity_commands", 3),
                ("joint_pos_rel", 31), ("joint_vel_rel", 31), ("last_action", 29))


class AgibotPolicy:
    """Réplica exacta de ``mc-rl/rl/sim2sim/sim2sim_x2.py`` (obs 500, acción 29, historial 5 por término)."""

    name = "agibot_oficial"
    controls_arms = True

    def __init__(self, model: mujoco.MjModel, onnx_path: Path | None = None, prefix: str = ""):
        onnx_path = Path(onnx_path or paths.agibot_policy())
        self.session = _session(onnx_path)
        self.input_name = self.session.get_inputs()[0].name
        self.state = _RobotState(model, AGIBOT_OBS_JOINTS, prefix)
        self.default_act = np.array([J.DEFAULT_POSE[n] for n in AGIBOT_ACTION_JOINTS])
        self.joints = AGIBOT_ACTION_JOINTS
        self.hist = _History(AGIBOT_TERMS, 5)
        self.last_action = np.zeros(29)
        self.count = 0
        self.path = onnx_path

    def reset(self) -> None:
        self.hist = _History(AGIBOT_TERMS, 5)
        for b in self.hist.buf.values():
            b[:] = 0.0
        self.hist.fresh = False          # sim2sim de AgiBot arranca con historial a cero
        self.last_action[:] = 0.0
        self.count = 0

    def act(self, data: mujoco.MjData, cmd: np.ndarray) -> np.ndarray:
        st = self.state
        frame = {
            "base_ang_vel": st.ang_vel(data) * 0.25,
            "projected_gravity": _projected_gravity(st.quat(data)),
            "velocity_commands": np.asarray(cmd, dtype=float),
            "joint_pos_rel": st.joint_pos_rel(data),
            "joint_vel_rel": st.joint_vel(data) * 0.05,
            "last_action": self.last_action,
        }
        obs = np.clip(self.hist.push(frame), -100, 100).astype(np.float32)[None]
        action = np.clip(self.session.run(None, {self.input_name: obs})[0][0].astype(float), -5, 5)
        if self.count < 5:          # calentamiento del historial, como en sim2sim_x2.py
            action[:] = 0.0
        self.count += 1
        self.last_action = action
        return self.default_act + 0.25 * action


def load_policy(kind: str, model: mujoco.MjModel, onnx_path: Path | None = None, prefix: str = ""):
    if kind in ("brazos_libres", "arms_free", "propia"):
        return ArmsFreePolicy(model, onnx_path, prefix)
    if kind in ("agibot", "agibot_oficial"):
        return AgibotPolicy(model, onnx_path, prefix)
    raise ValueError(f"policy desconocida: {kind}")
