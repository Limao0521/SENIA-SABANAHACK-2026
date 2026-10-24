"""Física del gemelo digital sin ROS: el "cuerpo" que en el robot real hay debajo de la HAL.

Cada grupo de articulaciones (pierna, cintura, brazo, cabeza) guarda la última ``JointCommand`` recibida y en
cada paso de 1 ms se aplica, como el driver de un motor:

    τ = kp·(q_ref − q) + kd·(q̇_ref − q̇) + τ_ff,   recortado al par máximo del eje

Sin órdenes, el par es 0 (el robot cuelga del pórtico). Igual que en el robot, la HAL no tiene timeout: la
última orden se mantiene hasta que llegue otra. Los dedos son servos de posición (``HandCommand.position``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import mujoco
import numpy as np

from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H
from x2_ultra.robot import tactile as T
from x2_ultra.twin import contract as C
from x2_ultra.twin.model import GANTRY, TIMESTEP, build_twin_model

TOUCH_FULL_SCALE = 10.0       # [N] fuerza de una región que satura sus celdas (código 255); sin calibrar
# Región del mensaje de AimDK -> sensor touch del modelo (model.builder.TOUCH_REGIONS).
SIM_TOUCH_REGION = {"palm": "palm", "thumb": "thumb", "index": "index", "middle": "middle", "ring": "ring",
                    "little": "pinky"}


@dataclass
class GroupCommand:
    """Última orden HAL de un grupo, en el orden de ``contract.JOINT_GROUPS``."""
    n: int
    q: np.ndarray = field(init=False)
    dq: np.ndarray = field(init=False)
    tau: np.ndarray = field(init=False)
    kp: np.ndarray = field(init=False)
    kd: np.ndarray = field(init=False)
    received: bool = False
    stamp: float = 0.0          # tiempo de simulación de la última orden

    def __post_init__(self):
        for name in ("q", "dq", "tau", "kp", "kd"):
            setattr(self, name, np.zeros(self.n))


class TwinPhysics:
    def __init__(self, scene: str = "mesa", timestep: float = TIMESTEP, gantry: bool = True,
                 model: mujoco.MjModel | None = None):
        self.model = model if model is not None else build_twin_model(scene, timestep)
        self.data = mujoco.MjData(self.model)
        m = self.model
        jid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)
        aid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, n)

        self.groups = dict(C.JOINT_GROUPS)
        self.qadr = {g: np.array([m.jnt_qposadr[jid(n)] for n in names]) for g, names in self.groups.items()}
        self.dadr = {g: np.array([m.jnt_dofadr[jid(n)] for n in names]) for g, names in self.groups.items()}
        self.act = {g: np.array([aid(n) for n in names]) for g, names in self.groups.items()}
        self.tau_max = {g: np.array([J.EFFORT_LIMIT[n] for n in names]) for g, names in self.groups.items()}
        self.cmd = {g: GroupCommand(len(names)) for g, names in self.groups.items()}
        # Vectores concatenados para el PD de los 31 ejes en una sola operación.
        order = list(self.groups)
        self._all_q = np.concatenate([self.qadr[g] for g in order])
        self._all_d = np.concatenate([self.dadr[g] for g in order])
        self._all_a = np.concatenate([self.act[g] for g in order])
        self._all_tmax = np.concatenate([self.tau_max[g] for g in order])
        self._slices, start = {}, 0
        for g in order:
            self._slices[g] = slice(start, start + len(self.groups[g]))
            start += len(self.groups[g])

        hand_names = H.LEFT_HAND_JOINTS + H.RIGHT_HAND_JOINTS
        self.hand_q = np.array([m.jnt_qposadr[jid(n)] for n in hand_names])
        self.hand_d = np.array([m.jnt_dofadr[jid(n)] for n in hand_names])
        self.hand_act = np.array([aid(n) for n in hand_names])
        self.hand_lo = m.actuator_ctrlrange[self.hand_act, 0].copy()
        self.hand_hi = m.actuator_ctrlrange[self.hand_act, 1].copy()
        self.hand_target = np.zeros(len(hand_names))
        self.hand_received = False

        self._sensor = {s: (m.sensor_adr[i], m.sensor_dim[i])
                        for i in range(m.nsensor) if (s := mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_SENSOR, i))}
        self.pelvis = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        self.torso = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
        self._push_force, self._push_steps = np.zeros(3), 0
        self.gantry_id = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_EQUALITY, GANTRY)
        self.reset(gantry=gantry)

    # ------------------------------------------------------------------------------------------
    def reset(self, gantry: bool = True) -> None:
        mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
        for c in self.cmd.values():
            c.__post_init__()
            c.received = False
        self.hand_target[:] = 0.0
        self.hand_received = False
        mujoco.mj_forward(self.model, self.data)
        self.set_gantry(gantry)

    @property
    def time(self) -> float:
        return float(self.data.time)

    def set_gantry(self, on: bool, height: float | None = None) -> None:
        """Sujeta la pelvis en su pose actual (o a ``height``) o la suelta. El weld de MuJoCo guarda la pose del
        mundo vista desde la pelvis: relpos = −Rᵀ·p, relquat = q*."""
        d, i = self.data, self.gantry_id
        if on:
            p = d.xpos[self.pelvis].copy()
            if height is not None:
                p[2] = height
            q = d.xquat[self.pelvis].copy()
            R = d.xmat[self.pelvis].reshape(3, 3)
            self.model.eq_data[i, 0:3] = 0.0
            self.model.eq_data[i, 3:6] = -R.T @ p
            self.model.eq_data[i, 6:10] = [q[0], -q[1], -q[2], -q[3]]
            self.model.eq_data[i, 10] = 1.0
        d.eq_active[i] = 1 if on else 0

    @property
    def gantry(self) -> bool:
        return bool(self.data.eq_active[self.gantry_id])

    # --- órdenes ------------------------------------------------------------------------------
    def set_joint_command(self, group: str, q, dq, tau, kp, kd) -> None:
        c = self.cmd[group]
        c.q[:], c.dq[:], c.tau[:], c.kp[:], c.kd[:] = q, dq, tau, kp, kd
        c.received, c.stamp = True, self.time

    def set_hand_targets(self, positions) -> None:
        """20 posiciones [rad] (izquierda 10 + derecha 10, orden de ``HandCommand[]``)."""
        self.hand_target[:] = np.clip(positions, self.hand_lo, self.hand_hi)
        self.hand_received = True

    # --- paso ----------------------------------------------------------------------------------
    def joint_torques(self) -> np.ndarray:
        d = self.data
        q, dq = d.qpos[self._all_q], d.qvel[self._all_d]
        tau = np.zeros(len(self._all_q))
        for g, s in self._slices.items():
            c = self.cmd[g]
            if c.received:
                tau[s] = c.kp * (c.q - q[s]) + c.kd * (c.dq - dq[s]) + c.tau
        return np.clip(tau, -self._all_tmax, self._all_tmax)

    def push(self, force, duration: float = 0.15) -> None:
        """Empujón en el torso (solo simulación: ``/aima/sim/option/command`` "push fx fy [fz]")."""
        self._push_force = np.asarray(force, dtype=float)
        self._push_steps = int(duration / self.model.opt.timestep)

    def step(self, n: int = 1) -> None:
        d = self.data
        d.ctrl[self.hand_act] = self.hand_target
        for _ in range(n):
            d.ctrl[self._all_a] = self.joint_torques()
            if self._push_steps > 0:
                d.xfrc_applied[self.torso, :3] = self._push_force
                self._push_steps -= 1
            elif self._push_steps == 0 and self._push_force.any():
                d.xfrc_applied[self.torso, :3] = 0.0
                self._push_force = np.zeros(3)
            mujoco.mj_step(self.model, d)

    # --- estados -------------------------------------------------------------------------------
    def joint_state(self, group: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        d = self.data
        return (d.qpos[self.qadr[group]].copy(), d.qvel[self.dadr[group]].copy(),
                d.actuator_force[self.act[group]].copy())

    def sensor(self, name: str) -> np.ndarray:
        adr, dim = self._sensor[name]
        return self.data.sensordata[adr:adr + dim].copy()

    def imu(self, name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(cuaternión wxyz en el mundo, giróscopo y acelerómetro en el marco de la IMU) de 'torso' o 'chest'."""
        return self.sensor(f"imu_{name}_quat"), self.sensor(f"imu_{name}_gyro"), self.sensor(f"imu_{name}_acc")

    def sensor_imu(self, site: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """IMU de un sensor ('imu_rgbd', 'imu_lidar')."""
        return self.sensor(f"{site}_quat"), self.sensor(f"{site}_gyro"), self.sensor(f"{site}_acc")

    def odom(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Como sim_mujoco: pose del punto de la IMU de pelvis en el mundo y velocidades en su marco."""
        return (self.sensor("imu_torso_pos"), self.sensor("imu_torso_quat"), self.sensor("imu_torso_vel"),
                self.sensor("imu_torso_gyro"))

    def hand_state(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        d = self.data
        return d.qpos[self.hand_q].copy(), d.qvel[self.hand_d].copy(), d.actuator_force[self.hand_act].copy()

    def touch_codes(self, side: str) -> dict[str, np.ndarray]:
        """Tacto de una mano ('L'/'R') con los campos de ``HandTouchSensorData`` (uint8 por celda). La fuerza de
        cada región simulada se reparte por igual entre sus celdas usadas (palma 25 de 36, yemas 16); el dorso
        no se simula y queda a 0."""
        out = {}
        for (region, n_cells), fld in zip(H.TACTILE_REGIONS, H.TACTILE_FIELDS):
            arr = np.zeros(n_cells, dtype=np.uint8)
            sim_region = SIM_TOUCH_REGION.get(region)
            if sim_region:
                force = float(self.sensor(f"{side}_touch_{sim_region}")[0])
                code = int(np.clip(force / TOUCH_FULL_SCALE * 255.0, 0, 255))
                arr[:T.USED_CELLS[region]] = code
            out[fld] = arr
        return out

    def qpos_snapshot(self) -> np.ndarray:
        return self.data.qpos.copy()
