"""Motor del escenario: una física MuJoCo con los dos robots, cada uno con su policy de marcha y sus objetivos.

Cada ``RobotRig`` hace con su robot lo mismo que ``sim.engine.SimEngine`` con el suyo: la policy "brazos libres"
mueve piernas y cintura a partir de (vx, vy, wz) suavizados, y los brazos, la cabeza y las manos siguen los
objetivos del usuario con límite de velocidad. La policy es la misma para los dos: el X2 EDU es el mismo cuerpo
sin cuello y ~4 kg más ligero, y la policy lee su cabeza en la postura por defecto.

    eng = ShowEngine()
    eng.rigs["edu"].set_velocity_target(0.4, 0, 0)
    eng.rigs["ultra"].play_pose("saludar")
    eng.run_for(5.0)            # sin hilo
    eng.start()                 # o en tiempo real en un hilo (lo que hace la app)
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
from x2_ultra.control.poses import ARM_POSES, HRI_POSES, clamp_arms
from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H
from x2_ultra.show.dance import DanceTrack, load_clip
from x2_ultra.show.dog import DogRig
from x2_ultra.show.stage import DOG_PREFIX, ESCENARIO, Decor, StageRobot, build_stage_model
from x2_ultra.sim.engine import ARM_RATE, FALL_HEIGHT, FALL_TILT, HAND_RATE, HEAD_RATE, SpeedLimits, _wrap, _yaw

# --- Canal de waist HRI (W1): el delta de waist sobre la policy ya NO se aplica crudo. Cadena:
# bounds por eje → low-pass crítico de 2º orden (fc) → slew por eje → ganancia g(t) (rampa
# min-jerk en los bordes de turno/preset × factor de equilibrio tilt/margen-COM). La parte de
# yaw recortada por los bounds se reparte a shoulder_yaw (ambos brazos) y head_yaw (Ultra).
# Diagnóstico: out_diag/legs/REPORT.md (causa nº1 de las reacciones de piernas) y
# out_diag/bow/REPORT.md (fix (a): cap 0,10 + slew 0,06 rad/s → drift ≈ 0 en la venia).
WAIST_FC = 1.5                                        # Hz: low-pass críticamente amortiguado
WAIST_SLEW = np.array([0.4, 0.2, 0.2])                # rad/s por eje (yaw, pitch, roll)
WAIST_BOUNDS = np.array([0.10, 0.05, 0.03])           # rad por eje (canal SONIC; fix legs2 c_b10k07)
WAIST_SLEW_PRESET = np.array([0.4, 0.06, 0.06])       # rad/s: presets cinemáticos (venia, D2 fix a)
WAIST_BOUNDS_PRESET = np.array([0.18, 0.10, 0.05])    # rad: preset pitch cap 0,10
WAIST_GATE_S = 0.4                                    # s: rampa minimum-jerk de la ganancia g(t)
WAIST_TILT_LO, WAIST_TILT_HI = 3.0, 6.0               # °: el factor de equilibrio cae linealmente 1→0
WAIST_MARGIN_MM = 30.0                                # mm: margen COM→soporte bajo el que cae linealmente
WAIST_SPILL_ARM = 0.10                                # rad máx: yaw recortado → cada shoulder_yaw
WAIST_SPILL_HEAD = 0.15                               # rad máx: yaw recortado → head yaw (Ultra)
WAIST_SPILL_RATE = 0.5                                # rad/s: slew del reparto

# --- T4 (hri_runtime/motion_online): límites de velocidad por articulación, HRI_RUNTIME_SPEC.
# Fuera de emotes: 10 rad/s por articulación (emotes por spec: cadera roll/pitch 40, cadera yaw
# 30, cintura 20, brazos 20, cabeza 30). Backstop del simulador: los rates existentes (ARM_RATE,
# HEAD_RATE, slew del WaistFilter) ya están muy por debajo, así que no cambia el comportamiento;
# solo hace explícito el límite de la spec en los comandos de brazos/waist/cabeza.
SPEC_VEL_JOINT = 10.0                                 # rad/s por articulación (no emote)
SPEC_VEL_ARMS = np.full(len(J.ARM_JOINTS), SPEC_VEL_JOINT)
SPEC_VEL_WAIST = np.full(len(J.WAIST_JOINTS), SPEC_VEL_JOINT)
SPEC_VEL_HEAD = np.full(len(J.HEAD_JOINTS), SPEC_VEL_JOINT)


def spec_vel_clip(old: np.ndarray, new: np.ndarray, vmax, dt: float) -> np.ndarray:
    """Acota a ``vmax`` (rad/s, por articulación) la velocidad del comando old→new en un tick."""
    step = np.asarray(vmax, dtype=float) * dt
    return old + np.clip(np.asarray(new, dtype=float) - old, -step, step)


class WaistFilter:
    """Filtro del canal de waist: bounds por eje + low-pass crítico de 2º orden + slew por eje.

    Puro (sin MuJoCo) para ser testeable: ``step(target, dt, gain)`` devuelve el delta aplicado
    y ``excess`` lo que los bounds recortaron (para el reparto a hombros/cabeza). Modo *gentle*
    (presets cinemáticos, p. ej. la venia VIP): cap de pitch 0,10 rad y slew 0,06 rad/s.
    """

    def __init__(self, n: int = 3, fc: float = WAIST_FC):
        self.n = int(n)
        self.w = 2.0 * math.pi * fc
        self.x = np.zeros(self.n)        # estado del low-pass
        self.v = np.zeros(self.n)        # velocidad del low-pass
        self.y = np.zeros(self.n)        # salida con slew (antes de ganancia)
        self.excess = np.zeros(self.n)   # target − target acotado (reparto de yaw)
        self.gentle = False
        self._limits()

    def _limits(self) -> None:
        b, s = (WAIST_BOUNDS_PRESET, WAIST_SLEW_PRESET) if self.gentle else (WAIST_BOUNDS, WAIST_SLEW)
        self.bounds = b[:self.n].copy()
        self.slew = s[:self.n].copy()

    def set_gentle(self, gentle: bool) -> None:
        if bool(gentle) != self.gentle:
            self.gentle = bool(gentle)
            self._limits()

    def reset(self) -> None:
        self.x[:] = self.v[:] = self.y[:] = self.excess[:] = 0.0

    def step(self, target: np.ndarray, dt: float, gain: float = 1.0) -> np.ndarray:
        t = np.asarray(target, dtype=float).reshape(-1)[:self.n]
        u = np.clip(t, -self.bounds, self.bounds)
        self.excess = t - u
        self.v += (self.w * self.w * (u - self.x) - 2.0 * self.w * self.v) * dt
        self.x = np.clip(self.x + self.v * dt, -self.bounds, self.bounds)
        self.y += np.clip(self.x - self.y, -self.slew * dt, self.slew * dt)
        return self.y * gain


@dataclass
class RigInfo:
    state: str = "activo"
    cmd: np.ndarray = field(default_factory=lambda: np.zeros(3))
    base_pos: np.ndarray = field(default_factory=lambda: np.zeros(3))
    yaw: float = 0.0
    pose: str = ""
    falls: int = 0
    dance: str = ""               # "Kung Fu 12.3/30.0 s" mientras baila


class RobotRig:
    def __init__(self, model: mujoco.MjModel, robot: StageRobot, policy: str = "brazos_libres",
                 policy_path: Path | None = None, speed: SpeedLimits | None = None, floor_z: float = 0.0):
        self.robot = robot
        self.floor_z = floor_z          # altura del suelo bajo el robot (la tarima), para detectar caídas
        self.model = model
        p = robot.prefix
        self.policy = load_policy(policy, model, policy_path, prefix=p)
        self.speed = speed or SpeedLimits()
        aid = lambda n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, p + n)
        jid = lambda n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, p + n)
        body_joints = J.BODY_JOINTS if robot.has_head else J.EDU_BODY_JOINTS
        self.body_joints = body_joints
        self.dance = None               # show.dance.DanceTrack mientras baila (sin policy)
        self.act_policy = np.array([aid(n) for n in self.policy.joints])
        self.act_waist = np.array([aid(n) for n in J.WAIST_JOINTS])
        # Delta de waist sobre la policy (gestos HRI): solo se aplica con el robot parado y pasa
        # por WaistFilter + ganancia g(t) + factor de equilibrio (ver constantes WAIST_* arriba).
        nw = len(self.act_waist)
        self.waist_target = np.zeros(nw)        # delta pedido (recortado a waist_delta_lim)
        self.waist_delta = np.zeros(nw)         # delta aplicado (filtrado × ganancia): logger/diag
        self.waist_delta_lim = np.array([0.35, 0.15, 0.15])[:nw]
        self._wf = WaistFilter(nw)
        self._waist_gate = 1.0                  # ganancia g(t) actual (rampa min-jerk)
        self._gate_from = self._gate_to = 1.0
        self._gate_t0 = math.nan
        self._spill = np.zeros(2)               # reparto rate-limited [shoulder_yaw, head_yaw]
        self._arm_shyaw_idx = np.array([J.ARM_JOINTS.index(n) for n in
                                        ("left_shoulder_yaw_joint", "right_shoulder_yaw_joint")])
        foot_bodies = {mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, p + s + "_ankle_roll_link")
                       for s in ("left", "right")}
        self._foot_geoms = frozenset(g for g in range(model.ngeom)
                                     if model.geom_bodyid[g] in foot_bodies)
        self._root_body = model.jnt_bodyid[jid("floating_base_joint")]
        self.act_arms = np.array([aid(n) for n in J.ARM_JOINTS])
        self.act_head = np.array([aid(n) for n in J.HEAD_JOINTS]) if robot.has_head else np.zeros(0, int)
        self.act_hands = np.array([aid(n) for n in H.HAND_JOINTS]) if robot.has_hands else np.zeros(0, int)
        self.act_body = np.array([aid(n) for n in body_joints])
        self.q_arms = np.array([model.jnt_qposadr[jid(n)] for n in J.ARM_JOINTS])
        base = jid("floating_base_joint")
        self.qbase, self.vbase = model.jnt_qposadr[base], model.jnt_dofadr[base]
        self.torso = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, p + "torso_link")
        self.info = RigInfo()
        self._gain0 = model.actuator_gainprm[self.act_body].copy()
        self._bias0 = model.actuator_biasprm[self.act_body].copy()

    # ---------------------------------------------------------------------------------- órdenes
    def reset(self, data: mujoco.MjData) -> None:
        m = self.model
        m.actuator_gainprm[self.act_body] = self._gain0
        m.actuator_biasprm[self.act_body] = self._bias0
        self.policy.reset()
        self.cmd = np.zeros(3)
        self.cmd_target = np.zeros(3)
        self.waist_target = np.zeros(len(self.act_waist))
        self.waist_delta = np.zeros(len(self.act_waist))
        self._wf.reset()
        self._wf.set_gentle(False)
        self._waist_gate = 1.0
        self._gate_from = self._gate_to = 1.0
        self._gate_t0 = math.nan
        self._spill[:] = 0.0
        self.arm_target = np.array([J.DEFAULT_POSE[n] for n in J.ARM_JOINTS])
        self.arm_cmd = self.arm_target.copy()
        self.head_target = np.zeros(len(self.act_head))
        self.head_cmd = np.zeros(len(self.act_head))
        self.hand_target = np.zeros(len(self.act_hands))
        self.hand_cmd = np.zeros(len(self.act_hands))
        self.anim = None
        self.anim_t0 = 0.0
        self.yaw_ref = _yaw(self.quat(data))
        self.state = "activo"
        self.info.pose = ""

    def set_velocity_target(self, vx: float, vy: float, wz: float) -> None:
        lim = S.CMD_LIMITS
        self.cmd_target = np.array([np.clip(vx, *lim["vx"]), np.clip(vy, *lim["vy"]), np.clip(wz, *lim["wz"])])

    def stop_walking(self) -> None:
        self.cmd_target = np.zeros(3)
        self.cmd = np.zeros(3)

    def set_arm_targets(self, q14: np.ndarray) -> None:
        self.arm_target = clamp_arms(np.asarray(q14, dtype=float))
        self.anim = None
        self.info.pose = ""

    def set_waist_delta(self, q3: np.ndarray) -> None:
        """Delta de waist (yaw, pitch, roll) pedido sobre la policy, solo efectivo parado.

        No se aplica crudo: ``control_tick`` lo filtra (``WaistFilter``) y lo multiplica por la
        ganancia g(t) de ``set_waist_gate`` y el factor de equilibrio (tilt/margen COM).
        """
        self.waist_target = np.clip(np.asarray(q3, dtype=float).reshape(-1)[:len(self.act_waist)],
                                    -self.waist_delta_lim, self.waist_delta_lim)

    def set_waist_gentle(self, gentle: bool) -> None:
        """Modo preset cinemático (venia VIP): pitch cap 0,10 rad y slew 0,06 rad/s (D2 fix a)."""
        self._wf.set_gentle(gentle)

    def set_waist_gate(self, on: bool) -> None:
        """Abre/cierra la ganancia g(t) del canal de waist con rampa minimum-jerk de 0,4 s."""
        tgt = 1.0 if on else 0.0
        if tgt != self._gate_to:
            self._gate_from, self._gate_to = self._waist_gate, tgt
            self._gate_t0 = math.nan

    def set_head_targets(self, yaw: float, pitch: float) -> None:
        if not self.robot.has_head:
            return
        lo = [J.JOINT_RANGE[n][0] for n in J.HEAD_JOINTS]
        hi = [J.JOINT_RANGE[n][1] for n in J.HEAD_JOINTS]
        self.head_target = np.clip([yaw, pitch], lo, hi)

    def set_hand_pose(self, side: str, pose: str) -> None:
        if not self.robot.has_hands:
            return
        for s in (("L", "R") if side == "LR" else (side,)):
            joints = H.LEFT_HAND_JOINTS if s == "L" else H.RIGHT_HAND_JOINTS
            lo = np.array([H.HAND_RANGE[n][0] for n in joints])
            hi = np.array([H.HAND_RANGE[n][1] for n in joints])
            sl = slice(0, 10) if s == "L" else slice(10, 20)
            self.hand_target[sl] = np.clip(H.pose_radians(s, pose), lo, hi)

    def play_pose(self, name: str, t: float) -> None:
        pose = (ARM_POSES | HRI_POSES)[name]
        self.arm_target = pose.target(0.0)
        self.anim = pose if pose.wave else None
        self.anim_t0 = t
        self.info.pose = name
        for side, hp in pose.hands.items():
            self.set_hand_pose(side, hp)

    # ------------------------------------------------------------------------------- estado
    def quat(self, data: mujoco.MjData) -> np.ndarray:
        return data.qpos[self.qbase + 3:self.qbase + 7]

    def _set_stiffness(self, on: bool) -> None:
        m = self.model
        if on:
            m.actuator_gainprm[self.act_body] = self._gain0
            m.actuator_biasprm[self.act_body] = self._bias0
        else:   # amortiguamiento: kp = 0, se conserva kd (como el modo DAMPING del robot)
            m.actuator_gainprm[self.act_body, 0] = 0.0
            m.actuator_biasprm[self.act_body, 1] = 0.0

    def _smooth_cmd(self, data: mujoco.MjData, dt: float) -> None:
        sp = self.speed
        target = self.cmd_target.copy()
        yaw = _yaw(self.quat(data))
        if abs(target[2]) > 1e-3 or np.linalg.norm(target[:2]) < 1e-3:
            self.yaw_ref = yaw      # girando o parado: el rumbo de referencia sigue al robot
        else:                       # andando recto: mantener el rumbo
            target[2] = float(np.clip(1.5 * _wrap(self.yaw_ref - yaw), -0.35, 0.35))
        step = np.array([sp.lin_accel, sp.lin_accel, sp.ang_accel]) * dt
        self.cmd = self.cmd + np.clip(target - self.cmd, -step, step)

    def _gate_gain(self, t: float) -> float:
        """g(t): rampa minimum-jerk (0,4 s) entre ``_gate_from`` y ``_gate_to``."""
        if math.isnan(self._gate_t0):
            if self._gate_from == self._gate_to:
                self._waist_gate = self._gate_to
                return self._waist_gate
            self._gate_t0 = t
        x = min(1.0, max(0.0, (t - self._gate_t0) / WAIST_GATE_S))
        s = x * x * x * (10.0 + x * (-15.0 + 6.0 * x))       # min-jerk quintic 0→1
        self._waist_gate = self._gate_from + (self._gate_to - self._gate_from) * s
        if x >= 1.0:
            self._gate_from = self._gate_to
            self._gate_t0 = math.nan
        return self._waist_gate

    def _balance_scale(self, data: mujoco.MjData) -> float:
        """Factor 0-1: cae linealmente con tilt > 3° y con margen COM→soporte < 30 mm."""
        q = self.quat(data)
        up_z = 1 - 2 * (q[1] ** 2 + q[2] ** 2)
        tilt = math.degrees(math.acos(float(np.clip(up_z, -1.0, 1.0))))
        g = float(np.clip((WAIST_TILT_HI - tilt) / (WAIST_TILT_HI - WAIST_TILT_LO), 0.0, 1.0))
        com = data.subtree_com[self._root_body]
        lo = np.array([np.inf, np.inf])
        hi = np.array([-np.inf, -np.inf])
        for i in range(data.ncon):                            # soporte: contactos de los pies
            c = data.contact[i]
            if c.geom1 in self._foot_geoms or c.geom2 in self._foot_geoms:
                lo = np.minimum(lo, c.pos[:2])
                hi = np.maximum(hi, c.pos[:2])
        if np.all(lo <= hi):
            margin_mm = 1000.0 * float(np.min(np.concatenate((com[:2] - lo, hi - com[:2]))))
            g = min(g, float(np.clip(margin_mm / WAIST_MARGIN_MM, 0.0, 1.0)))
        return g

    def control_tick(self, data: mujoco.MjData) -> None:
        d, dt = data, S.POLICY_DT
        if self.state == "activo":
            self._smooth_cmd(d, dt)
            d.ctrl[self.act_policy] = self.policy.act(d, self.cmd)
            standing = float(np.max(np.abs(self.cmd))) < 0.05
            gate = self._gate_gain(d.time)
            gain = gate * (self._balance_scale(d) if standing and np.any(self._wf.y) else 1.0)
            self.waist_delta = self._wf.step(self.waist_target, dt, gain)
            if standing and np.any(self.waist_delta):
                d.ctrl[self.act_waist] = spec_vel_clip(d.ctrl[self.act_waist],
                                                       d.ctrl[self.act_waist] + self.waist_delta,
                                                       SPEC_VEL_WAIST, dt)
            # yaw recortado por los bounds → shoulder_yaw (ambos) y head_yaw (Ultra), con slew
            spill = self._wf.excess[0] * gate if self._wf.n and standing else 0.0
            tgt_spill = np.array([np.clip(spill, -WAIST_SPILL_ARM, WAIST_SPILL_ARM),
                                  np.clip(spill, -WAIST_SPILL_HEAD, WAIST_SPILL_HEAD)])
            self._spill += np.clip(tgt_spill - self._spill, -WAIST_SPILL_RATE * dt,
                                   WAIST_SPILL_RATE * dt)
            if not self.policy.controls_arms:
                target = self.anim.target(d.time - self.anim_t0) if self.anim is not None else self.arm_target
                if self._spill[0] != 0.0:
                    target = target.copy()
                    target[self._arm_shyaw_idx] += self._spill[0]
                    target = clamp_arms(target)
                self.arm_cmd += np.clip(target - self.arm_cmd, -ARM_RATE * dt, ARM_RATE * dt)
                d.ctrl[self.act_arms] = spec_vel_clip(d.ctrl[self.act_arms], self.arm_cmd,
                                                      SPEC_VEL_ARMS, dt)
            head_target = self.head_target
            if len(self.act_head) and self._spill[1] != 0.0:
                head_target = head_target.copy()
                lo, hi = J.JOINT_RANGE["head_yaw_joint"]
                head_target[0] = float(np.clip(head_target[0] + self._spill[1], lo, hi))
            self.head_cmd += np.clip(head_target - self.head_cmd, -HEAD_RATE * dt, HEAD_RATE * dt)
            if len(self.act_head):                # el Edu no tiene cabeza
                d.ctrl[self.act_head] = spec_vel_clip(d.ctrl[self.act_head], self.head_cmd,
                                                      SPEC_VEL_HEAD, dt)
            q = self.quat(d)
            up_z = 1 - 2 * (q[1] ** 2 + q[2] ** 2)
            if d.qpos[self.qbase + 2] - self.floor_z < FALL_HEIGHT or up_z < math.cos(FALL_TILT):
                self.state = "caido"
                self.info.falls += 1
                self._set_stiffness(False)
        self.hand_cmd += np.clip(self.hand_target - self.hand_cmd, -HAND_RATE * dt, HAND_RATE * dt)
        d.ctrl[self.act_hands] = self.hand_cmd

    def update_info(self, data: mujoco.MjData) -> None:
        inf = self.info
        inf.state = "bailando" if self.dance is not None else self.state
        inf.dance = (f"{self.dance.name} {self.dance.clip_time(data.time):.1f}/{self.dance.clip.duration:.1f} s"
                     if self.dance is not None else "")
        inf.cmd = self.cmd.copy()
        inf.base_pos = data.qpos[self.qbase:self.qbase + 3].copy()
        inf.yaw = _yaw(self.quat(data))


class ShowEngine:
    def __init__(self, decor: Decor = ESCENARIO, policy: str = "brazos_libres", policy_path: Path | None = None,
                 timestep: float = 0.002):
        self.decor = decor
        self.model = build_stage_model(decor, timestep)
        self.data = mujoco.MjData(self.model)
        self.timestep = timestep
        self.decimation = max(1, int(round(S.POLICY_DT / timestep)))
        self.rigs = {r.key: RobotRig(self.model, r, policy, policy_path, floor_z=decor.floor_z)
                     for r in decor.robots}
        self.props = decor.props(self.model) if decor.props else None
        self.dog = DogRig(self.model, decor.dog, decor.floor_z, DOG_PREFIX) if decor.dog is not None else None
        self.tour = None                # show.guide.GuideTour en curso
        self.guest = None               # show.guest.Guest (se crea tras el primer reset: necesita la escena)
        self.speech: list[tuple[float, str, str]] = []   # (tiempo, robot, frase) para subtítulos y voz
        self.home = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_KEY, "home")
        self.lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._running = False
        self.paused = False
        self.rtf = 0.0
        self.reset()
        if decor.guest is not None:
            self.guest = decor.guest(self.model, self.data)

    def reset(self) -> None:
        with self.lock:
            mujoco.mj_resetDataKeyframe(self.model, self.data, self.home)
            mujoco.mj_forward(self.model, self.data)
            if self.tour is not None:
                self.tour.done = True
            self.tour = None
            if self.guest is not None:
                self.guest.reset(self.data)
            if self.dog is not None:
                self.dog.reset(self.data)
            for rig in self.rigs.values():
                rig.dance = None
                rig.reset(self.data)
                rig.update_info(self.data)
            self._wall0, self._sim0 = time.perf_counter(), self.data.time

    @property
    def sim_time(self) -> float:
        return self.data.time

    def play_pose(self, key: str, name: str) -> None:
        with self.lock:
            self.rigs[key].play_pose(name, self.data.time)

    def set_arm_targets(self, key: str, q14: np.ndarray) -> None:
        with self.lock:
            self.rigs[key].set_arm_targets(q14)

    def start_dance(self, key: str, path: Path, name: str | None = None) -> None:
        """El robot ``key`` baila el clip de LinkCraft ``path`` desde donde está (ver show.dance)."""
        self.start_dance_many((key,), path, name)

    def start_dance_many(self, keys: tuple[str, ...], path: Path, name: str | None = None) -> None:
        """Start several rigs at the same simulation time for a shared choreography."""
        clip = load_clip(Path(path))
        with self.lock:
            start = self.data.time
            for key in keys:
                rig = self.rigs[key]
                stand_height = self.model.key_qpos[self.home][rig.qbase + 2]
                rig.stop_walking()
                rig.dance = DanceTrack(self.model, self.data, rig.robot.prefix, rig.body_joints, clip,
                                       name or Path(path).stem, start, self.decor.floor_z, stand_height)

    def say(self, text: str, key: str) -> None:
        """Frase del robot ``key``: la ventana la muestra como subtítulo y la lee en voz alta si puede."""
        self.speech.append((self.data.time, key, text))

    def start_tour(self, tour) -> None:
        with self.lock:
            if self.tour is not None:
                self.tour.stop(self)
            self.stop_dance(tour.key)
            self.tour = tour

    def stop_tour(self) -> None:
        with self.lock:
            if self.tour is not None:
                self.tour.stop(self)
                self.tour = None

    def touring(self, key: str) -> bool:
        return self.tour is not None and not self.tour.done and self.tour.key == key

    def stop_dance(self, key: str) -> None:
        with self.lock:
            rig = self.rigs[key]
            if rig.dance is not None:
                rig.dance.stop(self.data.time)

    def _end_dance(self, rig: RobotRig) -> None:
        """Fin del baile: el robot queda de pie en la postura por defecto y vuelve a la policy de marcha."""
        rig.dance.apply(self.data, self.data.time)
        rig.dance = None
        rig.reset(self.data)
        d = self.data
        d.ctrl[rig.act_body] = [J.DEFAULT_POSE[n] for n in rig.body_joints]

    def snapshot(self) -> np.ndarray:
        with self.lock:
            return self.data.qpos.copy()

    def snapshot_mocap(self) -> tuple[np.ndarray, np.ndarray]:
        """Posición y orientación de los cuerpos mocap (el huésped), para la vista."""
        with self.lock:
            return self.data.mocap_pos.copy(), self.data.mocap_quat.copy()

    def step_control(self) -> None:
        """Un ciclo de control (20 ms simulados) de los dos robots."""
        with self.lock:
            d = self.data
            dancers = [rig for rig in self.rigs.values() if rig.dance is not None]
            robots_xy = [d.qpos[r.qbase:r.qbase + 2].copy() for r in self.rigs.values()]
            if self.dog is not None:
                robots_xy.append(self.dog.xy)
            if self.guest is not None:
                self.guest.update(d, S.POLICY_DT, robots_xy)
            if self.props is not None:
                self.props.update(d, robots_xy + ([self.guest.xy] if self.guest is not None else []))
            if self.tour is not None and not self.tour.done:
                self.tour.update(self, d.time)
            if self.dog is not None:
                self.dog.tick(d, S.POLICY_DT)
            for rig in self.rigs.values():
                if rig.dance is None:
                    rig.control_tick(d)
            if dancers:             # la postura del baile se impone en cada paso; el resto integra física
                for _ in range(self.decimation):
                    for rig in dancers:
                        rig.dance.apply(d, d.time)
                    mujoco.mj_step(self.model, d)
                for rig in dancers:
                    if rig.dance.done(d.time):
                        self._end_dance(rig)
                    else:
                        rig.dance.apply(d, d.time)     # que la vista lo muestre en su postura, no tras un paso libre
            else:
                mujoco.mj_step(self.model, d, nstep=self.decimation)
            for rig in self.rigs.values():
                rig.update_info(d)
            wall = time.perf_counter() - self._wall0
            if wall > 0.5:
                self.rtf = (self.data.time - self._sim0) / wall
                self._wall0, self._sim0 = time.perf_counter(), self.data.time

    def run_for(self, seconds: float) -> None:
        for _ in range(int(round(seconds / S.POLICY_DT))):
            self.step_control()

    def _loop(self) -> None:
        next_t = time.perf_counter()
        while self._running:
            if self.paused:
                time.sleep(0.02)
                next_t = time.perf_counter()
                with self.lock:
                    self._wall0, self._sim0 = next_t, self.data.time
                continue
            self.step_control()
            next_t += S.POLICY_DT
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.25:      # vamos atrasados: no acumular deuda
                next_t = time.perf_counter()

    def start(self) -> None:
        if self._thread is None:
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="x2-escenario", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
