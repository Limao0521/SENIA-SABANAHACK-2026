"""Motor de simulación del X2 Ultra-N: física MuJoCo en tiempo real + policy de marcha + objetivos de usuario.

Separado de la interfaz para poder usarlo sin ventana (pruebas, evaluación, grabación de vídeo):

    eng = SimEngine(scene="mesa")
    eng.set_velocity_target(0.4, 0.0, 0.0)
    eng.play_pose("saludar")
    eng.run_for(5.0)            # sin hilo, tan rápido como se pueda
    eng.start()                 # o en tiempo real en un hilo (lo que hace la app)

Capas de control (cada 20 ms):
  marcha   -> la policy calcula piernas + cintura a partir de (vx, vy, wz) suavizados
  brazos   -> objetivo del usuario (sliders / IK / poses) con límite de velocidad articular
  cabeza   -> objetivo del usuario
  manos    -> 20 objetivos de dedos (OmniHand), con límite de velocidad
Todo se aplica como consigna de los actuadores de posición del modelo.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

from x2_ultra.control import obs_spec as S
from x2_ultra.control.locomotion import load_policy
from x2_ultra.control.poses import ARM_POSES, clamp_arms
from x2_ultra.model.builder import TOUCH_REGIONS, BuildOptions, build_model
from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H

ARM_RATE = 2.5      # rad/s, límite de velocidad de los objetivos de brazo
HEAD_RATE = 2.0
HAND_RATE = 6.0
FALL_HEIGHT = 0.40  # m (pelvis)
FALL_TILT = 1.0     # rad


@dataclass
class SpeedLimits:
    vx_forward: float = 0.6
    vx_backward: float = 0.4
    vy: float = 0.3
    wz: float = 1.0               # en el sitio la policy gira poco por debajo de ~0,6 rad/s: Q/E piden el máximo
    run_factor: float = 1.6       # Mayús: más rápido (se recorta al rango de entrenamiento; el giro ya va al máximo)
    lin_accel: float = 1.2        # m/s²
    ang_accel: float = 2.5        # rad/s²


@dataclass
class EngineInfo:
    state: str = "activo"
    sim_time: float = 0.0
    rtf: float = 0.0
    cmd: np.ndarray = field(default_factory=lambda: np.zeros(3))
    cmd_target: np.ndarray = field(default_factory=lambda: np.zeros(3))
    base_pos: np.ndarray = field(default_factory=lambda: np.zeros(3))
    base_vel: np.ndarray = field(default_factory=lambda: np.zeros(3))
    yaw: float = 0.0
    touch: dict = field(default_factory=dict)
    policy: str = ""
    pose: str = ""
    falls: int = 0


def _yaw(quat: np.ndarray) -> float:
    w, x, y, z = quat
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class SimEngine:
    def __init__(self, scene: str = "mesa", policy: str = "brazos_libres", policy_path: Path | None = None,
                 timestep: float = 0.002, speed: SpeedLimits | None = None):
        self.model = build_model(BuildOptions(scene=scene, timestep=timestep))
        self.data = mujoco.MjData(self.model)
        m = self.model
        self.timestep = timestep
        self.decimation = max(1, int(round(S.POLICY_DT / timestep)))
        self.policy = load_policy(policy, m, policy_path)
        self.speed = speed or SpeedLimits()
        aid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, n)
        jq = lambda n: m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)]
        self.act_policy = np.array([aid(n) for n in self.policy.joints])
        self.act_arms = np.array([aid(n) for n in J.ARM_JOINTS])
        self.act_head = np.array([aid(n) for n in J.HEAD_JOINTS])
        self.act_hands = np.array([aid(n) for n in H.HAND_JOINTS])
        self.act_body = np.array([aid(n) for n in J.BODY_JOINTS])
        self.q_arms = np.array([jq(n) for n in J.ARM_JOINTS])
        self.q_hands = np.array([jq(n) for n in H.HAND_JOINTS])
        self.q_body = np.array([jq(n) for n in J.BODY_JOINTS])
        self.pelvis = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        self.torso = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
        self.touch_adr = {f"{s}_{r}": m.sensor_adr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SENSOR, f"{s}_touch_{r}")]
                          for s in ("L", "R") for r in TOUCH_REGIONS}
        self._gain0 = m.actuator_gainprm.copy()
        self._bias0 = m.actuator_biasprm.copy()
        self.home = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_KEY, "home")

        self.lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._running = False
        self.paused = False
        self.realtime_factor = 1.0
        self.heading_hold = True
        self.info = EngineInfo(policy=self.policy.name)
        self._push_force = np.zeros(3)
        self._push_steps = 0
        self.reset()

    # ---------------------------------------------------------------------------------- órdenes
    def reset(self) -> None:
        with self.lock:
            m, d = self.model, self.data
            mujoco.mj_resetDataKeyframe(m, d, self.home)
            m.actuator_gainprm[:] = self._gain0
            m.actuator_biasprm[:] = self._bias0
            mujoco.mj_forward(m, d)
            self.policy.reset()
            self.cmd = np.zeros(3)
            self.cmd_target = np.zeros(3)
            self.arm_target = np.array([J.DEFAULT_POSE[n] for n in J.ARM_JOINTS])
            self.arm_cmd = self.arm_target.copy()
            self.head_target = np.zeros(2)
            self.head_cmd = np.zeros(2)
            self.hand_target = np.zeros(len(H.HAND_JOINTS))
            self.hand_cmd = np.zeros(len(H.HAND_JOINTS))
            self.anim = None
            self.anim_t0 = 0.0
            self.yaw_ref = _yaw(d.qpos[3:7])
            self.state = "activo"
            self.info.pose = ""
            self._step_count = 0
            self._push_steps = 0
            d.xfrc_applied[:] = 0.0
            d.ctrl[self.act_body] = [J.DEFAULT_POSE[n] for n in J.BODY_JOINTS]
            d.ctrl[self.act_hands] = 0.0
            self._wall0 = time.perf_counter()
            self._sim0 = d.time

    def set_velocity_target(self, vx: float, vy: float, wz: float) -> None:
        lim = S.CMD_LIMITS
        self.cmd_target = np.array([np.clip(vx, *lim["vx"]), np.clip(vy, *lim["vy"]), np.clip(wz, *lim["wz"])])

    def stop_walking(self) -> None:
        self.cmd_target = np.zeros(3)
        self.cmd = np.zeros(3)

    def set_arm_targets(self, q14: np.ndarray, keep_animation: bool = False) -> None:
        with self.lock:
            self.arm_target = clamp_arms(np.asarray(q14, dtype=float))
            if not keep_animation:
                self.anim = None
                self.info.pose = ""

    def set_head_targets(self, yaw: float, pitch: float) -> None:
        lo = [J.JOINT_RANGE[n][0] for n in J.HEAD_JOINTS]
        hi = [J.JOINT_RANGE[n][1] for n in J.HEAD_JOINTS]
        self.head_target = np.clip([yaw, pitch], lo, hi)

    def set_hand_targets(self, side: str, q10: np.ndarray) -> None:
        joints = H.LEFT_HAND_JOINTS if side == "L" else H.RIGHT_HAND_JOINTS
        lo = np.array([H.HAND_RANGE[n][0] for n in joints])
        hi = np.array([H.HAND_RANGE[n][1] for n in joints])
        sl = slice(0, 10) if side == "L" else slice(10, 20)
        with self.lock:
            self.hand_target[sl] = np.clip(q10, lo, hi)

    def set_hand_pose(self, side: str, pose: str) -> None:
        for s in (("L", "R") if side == "LR" else (side,)):
            self.set_hand_targets(s, H.pose_radians(s, pose))

    def play_pose(self, name: str) -> None:
        pose = ARM_POSES[name]
        with self.lock:
            self.arm_target = pose.target(0.0)
            self.anim = pose if pose.wave else None
            self.anim_t0 = self.data.time
            self.info.pose = name
        for side, hp in pose.hands.items():
            self.set_hand_pose(side, hp)

    def push(self, direction: np.ndarray | None = None, force: float = 120.0, duration: float = 0.15) -> None:
        if direction is None:
            a = np.random.uniform(0, 2 * math.pi)
            direction = np.array([math.cos(a), math.sin(a), 0.0])
        with self.lock:
            self._push_force = np.asarray(direction, dtype=float) / np.linalg.norm(direction) * force
            self._push_steps = int(duration / self.timestep)

    # ------------------------------------------------------------------------------- consultas
    def arm_positions(self) -> np.ndarray:
        with self.lock:
            return self.data.qpos[self.q_arms].copy()

    def snapshot(self) -> tuple[np.ndarray, EngineInfo]:
        with self.lock:
            return self.data.qpos.copy(), self.info

    # ------------------------------------------------------------------------------- simulación
    def _set_stiffness(self, on: bool) -> None:
        m = self.model
        if on:
            m.actuator_gainprm[self.act_body] = self._gain0[self.act_body]
            m.actuator_biasprm[self.act_body] = self._bias0[self.act_body]
        else:   # amortiguamiento: kp = 0, se conserva kd (como el modo DAMPING del robot)
            m.actuator_gainprm[self.act_body, 0] = 0.0
            m.actuator_biasprm[self.act_body, 1] = 0.0

    def _smooth_cmd(self, dt: float) -> None:
        sp = self.speed
        target = self.cmd_target.copy()
        if self.heading_hold:
            yaw = _yaw(self.data.qpos[3:7])
            if abs(target[2]) > 1e-3 or np.linalg.norm(target[:2]) < 1e-3:
                self.yaw_ref = yaw      # girando o parado: el rumbo de referencia sigue al robot
            else:
                target[2] = float(np.clip(1.5 * _wrap(self.yaw_ref - yaw), -0.35, 0.35))
        step = np.array([sp.lin_accel, sp.lin_accel, sp.ang_accel]) * dt
        self.cmd = self.cmd + np.clip(target - self.cmd, -step, step)

    def _control_tick(self) -> None:
        m, d, dt = self.model, self.data, S.POLICY_DT
        if self.state == "activo":
            self._smooth_cmd(dt)
            d.ctrl[self.act_policy] = self.policy.act(d, self.cmd)
            if not self.policy.controls_arms:
                target = self.anim.target(d.time - self.anim_t0) if self.anim is not None else self.arm_target
                self.arm_cmd += np.clip(target - self.arm_cmd, -ARM_RATE * dt, ARM_RATE * dt)
                d.ctrl[self.act_arms] = self.arm_cmd
            self.head_cmd += np.clip(self.head_target - self.head_cmd, -HEAD_RATE * dt, HEAD_RATE * dt)
            d.ctrl[self.act_head] = self.head_cmd
            # caída: pelvis baja o tronco muy inclinado -> amortiguamiento
            q = d.qpos[3:7]
            up_z = 1 - 2 * (q[1] ** 2 + q[2] ** 2)
            if d.qpos[2] < FALL_HEIGHT or up_z < math.cos(FALL_TILT):
                self.state = "caido"
                self.info.falls += 1
                self._set_stiffness(False)
        self.hand_cmd += np.clip(self.hand_target - self.hand_cmd, -HAND_RATE * dt, HAND_RATE * dt)
        d.ctrl[self.act_hands] = self.hand_cmd

    def _update_info(self) -> None:
        d, inf = self.data, self.info
        inf.state = "pausa" if self.paused else self.state
        inf.sim_time = d.time
        inf.cmd = self.cmd.copy()
        inf.cmd_target = self.cmd_target.copy()
        inf.base_pos = d.qpos[0:3].copy()
        inf.base_vel = d.qvel[0:3].copy()
        inf.yaw = _yaw(d.qpos[3:7])
        inf.touch = {k: float(d.sensordata[a]) for k, a in self.touch_adr.items()}
        wall = time.perf_counter() - self._wall0
        if wall > 0.5:
            inf.rtf = (d.time - self._sim0) / wall
            self._wall0, self._sim0 = time.perf_counter(), d.time

    def step_control(self) -> None:
        """Un ciclo de control (20 ms simulados)."""
        with self.lock:
            self._control_tick()
            d = self.data
            if self._push_steps > 0:
                n = min(self._push_steps, self.decimation)
                d.xfrc_applied[self.torso, :3] = self._push_force
                mujoco.mj_step(self.model, d, nstep=n)
                d.xfrc_applied[self.torso, :3] = 0.0
                self._push_steps -= n
                if n < self.decimation:
                    mujoco.mj_step(self.model, d, nstep=self.decimation - n)
            else:
                mujoco.mj_step(self.model, d, nstep=self.decimation)
            self._step_count += 1
            self._update_info()

    def run_for(self, seconds: float) -> None:
        for _ in range(int(round(seconds / S.POLICY_DT))):
            self.step_control()

    def _loop(self) -> None:
        next_t = time.perf_counter()
        while self._running:
            if self.paused:
                with self.lock:
                    self._update_info()
                time.sleep(0.02)
                next_t = time.perf_counter()
                continue
            self.step_control()
            next_t += S.POLICY_DT / max(self.realtime_factor, 1e-3)
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.25:      # vamos atrasados: no acumular deuda
                next_t = time.perf_counter()

    def start(self) -> None:
        if self._thread is None:
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="x2-sim", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
