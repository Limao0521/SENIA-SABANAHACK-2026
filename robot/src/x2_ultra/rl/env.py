"""Entorno de entrenamiento vectorizado para la marcha "brazos libres" del X2 Ultra-N.

- Física: MuJoCo en CPU, miles de robots en paralelo con ``mujoco.rollout`` (hilos en C++), dt = 5 ms,
  4 subpasos por acción (policy a 50 Hz), PD dentro de MuJoCo (actuadores de posición).
- La red controla piernas + cintura (15 ejes). Brazos y cabeza siguen objetivos aleatorios suaves que el
  entorno cambia cada 0,5-3 s (con currículo de amplitud), así la policy aprende a mantener el equilibrio
  con cualquier postura de brazos, que luego elige el usuario en el simulador.
- Recompensas, aleatorización y terminaciones portadas de la receta de AgiBot
  (mc-rl ``velocity_env_cfg.py``, Apache-2.0), sin el término de desviación de brazos.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import mujoco
import numpy as np
import torch
from mujoco import rollout
from tensordict import TensorDict

from x2_ultra.control import obs_spec as S
from x2_ultra.model.builder import BuildOptions, build_robot_spec
from x2_ultra.robot import joints as J


def default_reward_weights() -> dict:
    """v2: base AgiBot + términos estándar de humanoides de Isaac Lab (G1/H1) para que aparezca la marcha.
    v1 (pesos de AgiBot tal cual + currículo de órdenes desde ±0,2 m/s) se quedaba quieta: ver docs/06."""
    return {
        "track_lin_vel_xy": 1.5,
        "track_ang_vel_z": 2.0,
        "termination": -200.0,
        "lin_vel_z": -1.0,
        "ang_vel_xy": -0.1,
        "joint_vel": -0.001,
        "joint_acc": -2.5e-7,
        "action_rate": -0.03,
        "joint_pos_limits": -2.0,
        "energy": -2e-5,
        "waist_deviation": -0.3,
        "hip_deviation": -0.15,
        "flat_orientation": -2.0,
        "base_height": -10.0,
        "gait": 0.5,
        "feet_air_time": 0.75,
        "feet_slide": -0.2,
        "feet_clearance": 0.5,
        "stand_still": -0.5,
        "undesired_contacts": -1.0,
        "imitation": 1.0,
    }


@dataclass
class EnvCfg:
    num_envs: int = 4096
    nthread: int = 32
    device: str = "cuda"
    seed: int = 0
    episode_length_s: float = 20.0
    n_variants: int = 128
    # órdenes de velocidad: todo el rango de obs_spec.CMD_LIMITS desde el principio
    cmd_resample_s: tuple = (4.0, 8.0)
    rel_standing: float = 0.12
    rel_turn_in_place: float = 0.3      # fracción de órdenes de solo giro (vx = vy = 0, |wz| ≥ 0,2)
    track_lin_std2: float = 0.1         # varianza del núcleo exp de seguimiento lineal (la de AgiBot)
    track_ang_std2: float = 0.1         # más estrecho que el lineal: sin esto la policy ignora giros < 0,6 rad/s
    lin_init: float = 1.0
    ang_init: float = 1.0
    # dificultad 0..1: escala empujones, retardo, aleatorización y amplitud de brazos; sube si no se cae
    difficulty_init: float = 0.0
    difficulty_step: float = 0.05
    feet_air_threshold: float = 0.4
    # brazos y cabeza aleatorios
    arm_hold_s: tuple = (0.5, 3.0)
    arm_move_s: tuple = (0.3, 1.5)
    arm_default_prob: float = 0.25
    arm_scale_init: float = 0.0     # amplitud de brazos = dificultad (0: brazos quietos en la postura por defecto)
    # aleatorización de dominio
    friction: tuple = (0.3, 1.0)
    torso_mass_add: tuple = (-1.0, 3.0)
    com_offset: float = 0.03
    gain_scale: tuple = (0.9, 1.1)
    push_interval_s: tuple = (3.0, 6.0)
    push_vel: float = 0.5
    delay_substeps: tuple = (1, 6)
    imu_bias: float = 0.05
    noise: dict = field(default_factory=lambda: {"ang_vel": 0.2, "gravity": 0.05, "joint_pos": 0.01,
                                                 "joint_vel": 1.5})
    # recompensas
    rewards: dict = field(default_factory=default_reward_weights)
    base_height_target: float = 0.66
    gait_period: float = 0.8
    gait_offsets: tuple = (0.0, 0.5)
    gait_threshold: float = 0.55
    feet_clearance_target: float = 0.035   # altura del sitio de la planta [m] (≈ 10 cm del tobillo)
    termination_height: float = 0.3
    termination_tilt: float = 0.8
    stand_still_cmd: float = 0.1
    # profesor (policy de AgiBot): imitación de sus acciones de piernas y cintura, que se apaga con la dificultad
    teacher: bool = True
    imitation_sigma: float = 1.0
    imitation_fade: float = 0.6


UNDESIRED_FLOOR_BODIES = ("pelvis", "torso_link", "left_hip_pitch_link", "right_hip_pitch_link",
                          "left_hip_roll_link", "right_hip_roll_link", "left_hip_yaw_link", "right_hip_yaw_link",
                          "left_knee_link", "right_knee_link", "left_elbow_link", "right_elbow_link",
                          "left_wrist_roll_link", "right_wrist_roll_link")


def build_train_model() -> mujoco.MjModel:
    spec = build_robot_spec(BuildOptions(variant="train", timestep=S.SIM_DT_TRAIN))
    spec.worldbody.add_geom(name="floor", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[0, 0, 0.05],
                            condim=3, friction=[1.0, 0.005, 0.0001])
    found_force, found = 1 | 2, 1
    for side in ("left", "right"):
        spec.add_sensor(name=f"{side}_foot_contact", type=mujoco.mjtSensor.mjSENS_CONTACT,
                        objtype=mujoco.mjtObj.mjOBJ_BODY, objname=f"{side}_ankle_roll_link",
                        reftype=mujoco.mjtObj.mjOBJ_GEOM, refname="floor", intprm=[found_force, 3, 1])
    for body in UNDESIRED_FLOOR_BODIES:
        spec.add_sensor(name=f"bad_{body}", type=mujoco.mjtSensor.mjSENS_CONTACT,
                        objtype=mujoco.mjtObj.mjOBJ_BODY, objname=body,
                        reftype=mujoco.mjtObj.mjOBJ_GEOM, refname="floor", intprm=[found, 0, 1])
    spec.add_sensor(name="bad_legs_cross", type=mujoco.mjtSensor.mjSENS_CONTACT,
                    objtype=mujoco.mjtObj.mjOBJ_XBODY, objname="left_hip_roll_link",
                    reftype=mujoco.mjtObj.mjOBJ_XBODY, refname="right_hip_roll_link", intprm=[found, 0, 1])
    for name in S.ACTION_JOINTS:
        spec.add_sensor(name=f"frc_{name}", type=mujoco.mjtSensor.mjSENS_ACTUATORFRC,
                        objtype=mujoco.mjtObj.mjOBJ_ACTUATOR, objname=name)
    return spec.compile()


def _quat_to_mat(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1),
    ], -2)


def _smoothstep(s: np.ndarray) -> np.ndarray:
    s = np.clip(s, 0.0, 1.0)
    return s * s * (3 - 2 * s)


class X2ArmsFreeEnv:
    """API compatible con ``rsl_rl.env.VecEnv`` (observaciones como TensorDict con grupos policy/critic)."""

    def __init__(self, cfg: EnvCfg):
        self.cfg = cfg
        self.num_envs = N = cfg.num_envs
        self.device = cfg.device
        self.num_actions = S.NUM_ACTIONS
        self.dt = S.POLICY_DT
        self.max_episode_length = int(round(cfg.episode_length_s / self.dt))
        self.rng = np.random.default_rng(cfg.seed)

        m = self.model = build_train_model()
        self.nstate = mujoco.mj_stateSize(m, mujoco.mjtState.mjSTATE_FULLPHYSICS)
        self.q0, self.v0 = 1, 1 + m.nq                   # offsets de qpos/qvel dentro del estado
        jid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)
        self.obs_qadr = np.array([m.jnt_qposadr[jid(n)] for n in S.OBS_JOINTS])
        self.obs_vadr = np.array([m.jnt_dofadr[jid(n)] for n in S.OBS_JOINTS])
        act_names = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_ACTUATOR, i) for i in range(m.nu)]
        self.act_policy = np.array([act_names.index(n) for n in S.ACTION_JOINTS])
        free_names = [n for n in J.ARM_JOINTS + J.HEAD_JOINTS]
        self.act_free = np.array([act_names.index(n) for n in free_names])
        self.obs_policy_idx = np.array([S.OBS_JOINTS.index(n) for n in S.ACTION_JOINTS])
        self.default_obs = np.array([J.DEFAULT_POSE[n] for n in S.OBS_JOINTS])
        self.default_act = np.array([J.DEFAULT_POSE[n] for n in S.ACTION_JOINTS])
        self.default_free = np.array([J.DEFAULT_POSE[n] for n in free_names])
        self.free_lo = np.array([J.JOINT_RANGE[n][0] for n in free_names]) + 0.05
        self.free_hi = np.array([J.JOINT_RANGE[n][1] for n in free_names]) - 0.05
        lo = np.array([J.JOINT_RANGE[n][0] for n in S.ACTION_JOINTS])
        hi = np.array([J.JOINT_RANGE[n][1] for n in S.ACTION_JOINTS])
        mid, half = (lo + hi) / 2, (hi - lo) / 2
        self.soft_lo, self.soft_hi = mid - 0.9 * half, mid + 0.9 * half
        self.waist_idx = np.array([S.ACTION_JOINTS.index(n) for n in J.WAIST_JOINTS])
        self.hip_idx = np.array([S.ACTION_JOINTS.index(n) for n in
                                 ("left_hip_roll_joint", "right_hip_roll_joint", "left_hip_yaw_joint", "right_hip_yaw_joint")])

        sadr = lambda n: m.sensor_adr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SENSOR, n)]
        self.s_foot_contact = [sadr(f"{s}_foot_contact") for s in ("left", "right")]
        self.s_foot_pos = [sadr(f"{s}_foot_pos") for s in ("left", "right")]
        self.s_foot_vel = [sadr(f"{s}_foot_vel") for s in ("left", "right")]
        self.s_bad = np.array([sadr(f"bad_{b}") for b in UNDESIRED_FLOOR_BODIES] + [sadr("bad_legs_cross")])
        self.s_frc = np.array([sadr(f"frc_{n}") for n in S.ACTION_JOINTS])

        # Estado inicial (de pie en la postura por defecto, pies apoyados).
        d = mujoco.MjData(m)
        mujoco.mj_resetData(m, d)
        d.qpos[self.obs_qadr] = self.default_obs
        d.qpos[2] = 1.0
        mujoco.mj_kinematics(m, d)
        foot_ids = [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, f"{s}_ankle_roll_link") for s in ("left", "right")]
        sphere_z = [d.geom_xpos[g][2] - m.geom_size[g][0] for g in range(m.ngeom)
                    if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_SPHERE and m.geom_bodyid[g] in foot_ids]
        d.qpos[2] -= min(sphere_z) - 0.002
        self.init_state = np.zeros(self.nstate)
        mujoco.mj_getState(m, d, self.init_state, mujoco.mjtState.mjSTATE_FULLPHYSICS)

        self._build_variants()
        self.datas = [mujoco.MjData(m) for _ in range(cfg.nthread)]
        self.teacher = None
        if cfg.teacher:
            from x2_ultra.rl.teacher import AgibotTeacher
            self.teacher = AgibotTeacher(N, cfg.device)
        self.teacher_act = np.zeros((N, S.NUM_ACTIONS))
        self._teacher_ref = self.teacher_act
        self.roller = rollout.Rollout(nthread=cfg.nthread)

        # Buffers por entorno.
        self.state = np.tile(self.init_state, (N, 1))
        self.variant = self.rng.integers(0, cfg.n_variants, N)
        self.models = [self.variants[i] for i in self.variant]
        self.ep_steps = np.zeros(N, dtype=np.int64)
        self.actions = np.zeros((N, S.NUM_ACTIONS))
        self.last_actions = np.zeros((N, S.NUM_ACTIONS))
        self.tgt_prev = np.tile(self.default_act, (N, 1))
        self.tgt_prev2 = self.tgt_prev.copy()
        self.delay = self.rng.integers(cfg.delay_substeps[0], cfg.delay_substeps[1] + 1, N)
        self.cmd = np.zeros((N, 3))
        self.cmd_timer = np.zeros(N)
        self.lin_range = cfg.lin_init
        self.ang_range = cfg.ang_init
        self.difficulty = cfg.difficulty_init
        self.arm_scale = cfg.arm_scale_init + (1 - cfg.arm_scale_init) * self.difficulty
        self.air_time = np.zeros((N, 2))
        self.contact_time = np.zeros((N, 2))
        nf = len(self.default_free)
        self.free_start = np.tile(self.default_free, (N, 1))
        self.free_goal = self.free_start.copy()
        self.free_t = np.zeros(N)
        self.free_move = np.ones(N)
        self.free_hold = np.zeros(N)
        self.free_target = self.free_start.copy()
        self.push_timer = self.rng.uniform(*cfg.push_interval_s, N)
        self.imu_bias = np.zeros(N)
        self.prev_joint_vel = np.zeros((N, S.NUM_ACTIONS))
        self.contact = np.zeros((N, 2), dtype=bool)
        self.hist = {name: np.zeros((N, S.HISTORY, dim)) for name, dim in S.OBS_TERMS}
        self.crit_hist = None
        self.reward_names = list(cfg.rewards)
        self.ep_sums = np.zeros((N, len(self.reward_names)))
        self.sens = np.zeros((N, m.nsensordata))
        self.log_buffer: dict = {}
        self.curr_stats = {"track_lin": [], "track_ang": [], "fell": []}
        self.global_step = 0
        del nf

        all_ids = np.arange(N)
        self._reset_envs(all_ids)
        self._step_physics(np.tile(self.default_act, (N, 1)), warm=True)
        self.obs = self._compute_obs(reset_ids=all_ids)

    # ------------------------------------------------------------------------------------------
    def _build_variants(self) -> None:
        """Variantes del modelo para la aleatorización de dominio, escaladas por la dificultad actual."""
        cfg, m, rng = self.cfg, self.model, self.rng
        k = getattr(self, "difficulty", cfg.difficulty_init)
        torso = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
        floor = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        robot_geoms = np.array([g for g in range(m.ngeom) if g != floor])
        mu_lo = 1.0 - k * (1.0 - cfg.friction[0])
        self.variants = []
        for _ in range(cfg.n_variants):
            v = copy.copy(m)
            mu = rng.uniform(mu_lo, cfg.friction[1])
            v.geom_friction[floor, 0] = mu
            v.geom_friction[robot_geoms, 0] = mu
            dm = k * rng.uniform(*cfg.torso_mass_add)
            scale = (v.body_mass[torso] + dm) / v.body_mass[torso]
            v.body_mass[torso] *= scale
            v.body_inertia[torso] *= scale
            v.body_ipos[1:] += k * rng.uniform(-cfg.com_offset, cfg.com_offset, 3)
            g = 1 + k * (rng.uniform(*cfg.gain_scale) - 1)
            for a in self.act_policy:
                v.actuator_gainprm[a, 0] *= g
                v.actuator_biasprm[a, 1] *= g
                v.actuator_biasprm[a, 2] *= 1 + k * (rng.uniform(*cfg.gain_scale) - 1)
            self.variants.append(v)

    @property
    def episode_length_buf(self) -> torch.Tensor:
        return torch.as_tensor(self.ep_steps)

    @episode_length_buf.setter
    def episode_length_buf(self, value) -> None:
        # rsl_rl lo sobrescribe con longitudes aleatorias al empezar (desincroniza los reinicios)
        self.ep_steps = np.asarray(torch.as_tensor(value).cpu().numpy(), dtype=np.int64).copy()

    # ------------------------------------------------------------------------------------------
    def get_observations(self) -> TensorDict:
        return self.obs

    def _sample_commands(self, ids: np.ndarray) -> None:
        if len(ids) == 0:
            return
        n = len(ids)
        cmd = np.zeros((n, 3))
        cmd[:, 0] = self.rng.uniform(max(-self.lin_range, S.CMD_LIMITS["vx"][0]),
                                     min(self.lin_range, S.CMD_LIMITS["vx"][1]), n)
        cmd[:, 1] = self.rng.uniform(max(-self.lin_range, S.CMD_LIMITS["vy"][0]),
                                     min(self.lin_range, S.CMD_LIMITS["vy"][1]), n)
        cmd[:, 2] = self.rng.uniform(max(-self.ang_range, S.CMD_LIMITS["wz"][0]),
                                     min(self.ang_range, S.CMD_LIMITS["wz"][1]), n)
        roll = self.rng.random(n)
        standing = roll < self.cfg.rel_standing
        turning = (roll >= self.cfg.rel_standing) & (roll < self.cfg.rel_standing + self.cfg.rel_turn_in_place)
        cmd[standing] = 0.0
        if turning.any():
            k = int(turning.sum())
            wz_max = min(self.ang_range, S.CMD_LIMITS["wz"][1])
            cmd[turning, :2] = 0.0
            cmd[turning, 2] = self.rng.choice([-1.0, 1.0], k) * self.rng.uniform(0.2, max(wz_max, 0.21), k)
        self.cmd[ids] = cmd
        self.cmd_timer[ids] = self.rng.uniform(*self.cfg.cmd_resample_s, n)

    def _sample_free_targets(self, ids: np.ndarray) -> None:
        """Nuevo objetivo de brazos/cabeza: por defecto, o uniforme en el rango escalado por el currículo."""
        if len(ids) == 0:
            return
        n, nf = len(ids), len(self.default_free)
        goal = self.default_free + self.arm_scale * (self.rng.uniform(self.free_lo, self.free_hi, (n, nf))
                                                     - self.default_free)
        keep_default = self.rng.random(n) < self.cfg.arm_default_prob
        goal[keep_default] = self.default_free
        # a veces un brazo se queda quieto mientras el otro se mueve
        left_still = self.rng.random(n) < 0.15
        right_still = self.rng.random(n) < 0.15
        goal[left_still, :7] = self.free_target[ids][left_still, :7]
        goal[right_still, 7:14] = self.free_target[ids][right_still, 7:14]
        self.free_start[ids] = self.free_target[ids]
        self.free_goal[ids] = np.clip(goal, self.free_lo, self.free_hi)
        self.free_t[ids] = 0.0
        self.free_move[ids] = self.rng.uniform(*self.cfg.arm_move_s, n)
        self.free_hold[ids] = self.free_move[ids] + self.rng.uniform(*self.cfg.arm_hold_s, n)

    def _reset_envs(self, ids: np.ndarray) -> None:
        if len(ids) == 0:
            return
        n = len(ids)
        st = np.tile(self.init_state, (n, 1))
        yaw = self.rng.uniform(-math.pi, math.pi, n)
        st[:, self.q0 + 3] = np.cos(yaw / 2)
        st[:, self.q0 + 6] = np.sin(yaw / 2)
        self.state[ids] = st
        self.variant[ids] = self.rng.integers(0, self.cfg.n_variants, n)
        for i in ids:
            self.models[i] = self.variants[self.variant[i]]
        dmax = self.cfg.delay_substeps[0] + int(round(self.difficulty * (self.cfg.delay_substeps[1] - self.cfg.delay_substeps[0])))
        self.delay[ids] = self.rng.integers(self.cfg.delay_substeps[0], dmax + 1, n)
        self.air_time[ids] = 0.0
        self.contact_time[ids] = 0.0
        self.actions[ids] = 0.0
        self.last_actions[ids] = 0.0
        self.tgt_prev[ids] = self.default_act
        self.tgt_prev2[ids] = self.default_act
        self.free_target[ids] = self.default_free
        self.free_start[ids] = self.default_free
        self.free_goal[ids] = self.default_free
        self.free_t[ids] = 0.0
        self.free_hold[ids] = self.rng.uniform(0.0, 1.0, n)
        self.free_move[ids] = 1.0
        self.push_timer[ids] = self.rng.uniform(*self.cfg.push_interval_s, n)
        self.imu_bias[ids] = self.rng.uniform(-self.cfg.imu_bias, self.cfg.imu_bias, n)
        self.prev_joint_vel[ids] = 0.0
        self.contact[ids] = True
        self.ep_steps[ids] = 0
        self.ep_sums[ids] = 0.0
        if self.teacher is not None:
            self.teacher.reset(ids)
        self._sample_commands(ids)

    # ------------------------------------------------------------------------------------------
    def _step_physics(self, tgt_new: np.ndarray, warm: bool = False) -> None:
        N, dec = self.num_envs, S.DECIMATION_TRAIN
        m = self.model
        ctrl = np.empty((N, dec, m.nu))
        # brazos y cabeza: interpolación suave hacia el objetivo actual
        s = _smoothstep(self.free_t / self.free_move)[:, None]
        self.free_target = self.free_start + s * (self.free_goal - self.free_start)
        ctrl[:, :, self.act_free] = self.free_target[:, None, :]
        # piernas y cintura con retardo de 1-6 subpasos (5-30 ms)
        j = np.arange(dec)[None, :]
        d = self.delay[:, None]
        use_new = j >= d
        use_prev = (j < d) & (j >= d - dec)
        pol = np.where(use_new[..., None], tgt_new[:, None, :],
                       np.where(use_prev[..., None], self.tgt_prev[:, None, :], self.tgt_prev2[:, None, :]))
        if warm:
            pol[:] = tgt_new[:, None, :]
        ctrl[:, :, self.act_policy] = pol
        if not hasattr(self, "_out_state"):
            self._out_state = np.empty((N, dec, self.nstate))
            self._out_sens = np.empty((N, dec, m.nsensordata))
            self._chunk = max(1, N // (self.cfg.nthread * 10))
        self.roller.rollout(self.models, self.datas, np.ascontiguousarray(self.state), ctrl, nstep=dec,
                            skip_checks=True, state=self._out_state, sensordata=self._out_sens,
                            chunk_size=self._chunk)
        self.state = self._out_state[:, -1, :].copy()
        self.sens = self._out_sens[:, -1, :].copy()
        self.tgt_prev2 = self.tgt_prev
        self.tgt_prev = tgt_new

    def step(self, actions: torch.Tensor):
        cfg, N = self.cfg, self.num_envs
        a = np.clip(actions.detach().to("cpu").numpy().astype(np.float64), -S.ACTION_CLIP, S.ACTION_CLIP)
        self.last_actions = self.actions
        self.actions = a
        tgt = self.default_act + S.ACTION_SCALE * a
        self._teacher_ref = self.teacher_act      # acción del profesor para la misma observación que vio la red
        if self.teacher is not None:
            # DAgger: el historial del profesor debe contener lo que se aplicó de verdad (piernas y cintura),
            # no lo que él habría hecho; si no, sus etiquetas no corresponden al estado real.
            self.teacher.last_action[:, self.teacher.amap] = a
        self._step_physics(tgt)
        self.global_step += 1
        self.ep_steps += 1
        self.free_t += self.dt
        self.cmd_timer -= self.dt
        self.push_timer -= self.dt

        rew, terms, died = self._rewards_and_terminations()
        timeout = self.ep_steps >= self.max_episode_length
        done = died | timeout

        # empujones (cambio brusco de velocidad de la base)
        push = (self.push_timer <= 0) & ~done
        if push.any():
            ids = np.nonzero(push)[0]
            vel = cfg.push_vel * min(1.0, self.difficulty / 0.6) if self.difficulty > 0.1 else 0.0
            self.state[ids, self.v0:self.v0 + 2] += self.rng.uniform(-vel, vel, (len(ids), 2))
            self.push_timer[ids] = self.rng.uniform(*cfg.push_interval_s, len(ids))
        self._sample_commands(np.nonzero((self.cmd_timer <= 0) & ~done)[0])
        self._sample_free_targets(np.nonzero((self.free_t >= self.free_hold) & ~done)[0])

        reset_ids = np.nonzero(done)[0]
        extras = {"time_outs": torch.as_tensor(timeout & ~died, device=self.device)}
        if len(reset_ids):
            self._log_episodes(reset_ids, died)
            self._reset_envs(reset_ids)
            # el estado reiniciado necesita un paso para tener sensores coherentes: se usa el inicial
        self._curriculum()
        extras["log"] = self.log_buffer
        self.obs = self._compute_obs(reset_ids=reset_ids)
        return (self.obs, torch.as_tensor(rew, dtype=torch.float32, device=self.device),
                torch.as_tensor(done, device=self.device), extras)

    # ------------------------------------------------------------------------------------------
    def _base_quantities(self):
        st = self.state
        quat = st[:, self.q0 + 3:self.q0 + 7]
        rot = _quat_to_mat(quat)
        lin_w = st[:, self.v0:self.v0 + 3]
        ang_b = st[:, self.v0 + 3:self.v0 + 6]
        lin_b = np.einsum("nji,nj->ni", rot, lin_w)
        grav = -rot[:, 2, :]                         # R^T·(0,0,-1)
        yaw = np.arctan2(rot[:, 1, 0], rot[:, 0, 0])
        ang_w = np.einsum("nij,nj->ni", rot, ang_b)
        return quat, rot, lin_w, lin_b, ang_b, ang_w, grav, yaw

    def _rewards_and_terminations(self):
        cfg, w = self.cfg, self.cfg.rewards
        st, sens = self.state, self.sens
        _, _, lin_w, lin_b, ang_b, ang_w, grav, yaw = self._base_quantities()
        q = st[:, self.q0 + self.obs_qadr]
        qd = st[:, self.v0 + self.obs_vadr]
        qa, qda = q[:, self.obs_policy_idx], qd[:, self.obs_policy_idx]
        cy, sy = np.cos(yaw), np.sin(yaw)
        v_yaw = np.stack([cy * lin_w[:, 0] + sy * lin_w[:, 1], -sy * lin_w[:, 0] + cy * lin_w[:, 1]], -1)
        contact = np.stack([sens[:, a] > 0 for a in self.s_foot_contact], -1)
        foot_z = np.stack([sens[:, a + 2] for a in self.s_foot_pos], -1)
        foot_vxy = np.stack([np.linalg.norm(sens[:, a:a + 2], axis=-1) for a in self.s_foot_vel], -1)
        frc = sens[:, self.s_frc]
        cmd_norm = np.linalg.norm(self.cmd, axis=-1)
        cmd_xy = np.linalg.norm(self.cmd[:, :2], axis=-1)
        self.contact_time = np.where(contact, self.contact_time + self.dt, 0.0)
        self.air_time = np.where(contact, 0.0, self.air_time + self.dt)
        in_mode = np.where(contact, self.contact_time, self.air_time)
        single = contact.sum(-1) == 1
        air_rew = np.clip(np.where(single, in_mode.min(-1), 0.0), None, cfg.feet_air_threshold) * (cmd_xy > 0.1)
        t = self.ep_steps * self.dt
        phase = (t % cfg.gait_period) / cfg.gait_period
        leg_phase = np.stack([(phase + o) % 1.0 for o in cfg.gait_offsets], -1)
        stance = leg_phase < cfg.gait_threshold
        r = {
            "track_lin_vel_xy": np.exp(-np.sum((self.cmd[:, :2] - v_yaw) ** 2, -1) / cfg.track_lin_std2),
            "track_ang_vel_z": np.exp(-(self.cmd[:, 2] - ang_w[:, 2]) ** 2 / cfg.track_ang_std2),
            "lin_vel_z": lin_b[:, 2] ** 2,
            "ang_vel_xy": np.sum(ang_b[:, :2] ** 2, -1),
            "joint_vel": np.sum(qda ** 2, -1),
            "joint_acc": np.sum(((qda - self.prev_joint_vel) / self.dt) ** 2, -1),
            "action_rate": np.sum((self.actions - self.last_actions) ** 2, -1),
            "joint_pos_limits": np.sum(np.clip(self.soft_lo - qa, 0, None) + np.clip(qa - self.soft_hi, 0, None), -1),
            "energy": np.sum(np.abs(qda) * np.abs(frc), -1),
            "waist_deviation": np.sum(np.abs(qa[:, self.waist_idx] - self.default_act[self.waist_idx]), -1),
            "hip_deviation": np.sum(np.abs(qa[:, self.hip_idx] - self.default_act[self.hip_idx]), -1),
            "flat_orientation": np.sum(grav[:, :2] ** 2, -1),
            "base_height": (st[:, self.q0 + 2] - cfg.base_height_target) ** 2,
            "gait": np.sum(~(stance ^ contact), -1) * (cmd_norm > 0.1),
            "feet_slide": np.sum(foot_vxy * contact, -1),
            "feet_clearance": np.exp(-np.sum((foot_z - cfg.feet_clearance_target) ** 2 * np.tanh(2 * foot_vxy), -1) / 0.05)
                              * (cmd_norm > 0.1),
            "feet_air_time": air_rew,
            "stand_still": np.sum(np.abs(qa[:, :12] - self.default_act[:12]), -1) * (cmd_norm < cfg.stand_still_cmd),
            "undesired_contacts": np.sum(sens[:, self.s_bad] > 0, -1).astype(float),
            "termination": np.zeros(self.num_envs),
            "imitation": np.exp(-np.mean((self.actions - self._teacher_ref) ** 2, -1) / cfg.imitation_sigma)
                         * max(0.0, 1.0 - self.difficulty / cfg.imitation_fade) * (self.teacher is not None),
        }
        self.prev_joint_vel = qda.copy()
        self.contact = contact
        bad = ~np.isfinite(st).all(-1) | (np.abs(st[:, self.v0:]).max(-1) > 1e3)
        died = (st[:, self.q0 + 2] < cfg.termination_height) | (grav[:, 2] > -math.cos(cfg.termination_tilt)) | bad
        r["termination"] = died.astype(float)
        terms = np.stack([r[k] * w[k] * self.dt for k in self.reward_names], -1)
        terms[bad] = 0.0
        terms[bad, self.reward_names.index("termination")] = w["termination"] * self.dt
        self.ep_sums += terms
        rew = terms.sum(-1)
        self._track = (r["track_lin_vel_xy"], r["track_ang_vel_z"])
        if bad.any():
            ids = np.nonzero(bad)[0]
            self.state[ids] = self.init_state
        return rew, terms, died

    def _log_episodes(self, ids: np.ndarray, died: np.ndarray) -> None:
        ep_len_s = np.maximum(self.ep_steps[ids], 1) * self.dt
        log = {}
        for k, name in enumerate(self.reward_names):
            log[f"Episode_Reward/{name}"] = float(np.mean(self.ep_sums[ids, k] / self.cfg.episode_length_s))
        log["Episode/length_s"] = float(np.mean(ep_len_s))
        log["Episode/fell"] = float(np.mean(died[ids]))
        self.curr_stats["track_lin"].extend((self.ep_sums[ids, self.reward_names.index("track_lin_vel_xy")]
                                             / self.cfg.episode_length_s).tolist())
        self.curr_stats["track_ang"].extend((self.ep_sums[ids, self.reward_names.index("track_ang_vel_z")]
                                             / self.cfg.episode_length_s).tolist())
        self.curr_stats["fell"].extend(died[ids].astype(float).tolist())
        self.log_buffer = log

    def _curriculum(self) -> None:
        """Cada 20 s simulados: sube la dificultad si casi no se cae y la baja si se cae mucho."""
        if self.global_step % self.max_episode_length != 0 or not self.curr_stats["fell"]:
            return
        fell = float(np.mean(self.curr_stats["fell"]))
        old = self.difficulty
        if fell < 0.2:
            self.difficulty = min(1.0, self.difficulty + self.cfg.difficulty_step)
        elif fell > 0.5:
            self.difficulty = max(0.0, self.difficulty - self.cfg.difficulty_step)
        if self.difficulty != old:
            self.arm_scale = self.cfg.arm_scale_init + (1 - self.cfg.arm_scale_init) * self.difficulty
            self._build_variants()
        self.curr_stats = {"track_lin": [], "track_ang": [], "fell": []}
        self.log_buffer.update({"Curriculum/difficulty": self.difficulty, "Curriculum/arm_scale": self.arm_scale,
                                "Curriculum/fell_window": fell})

    # ------------------------------------------------------------------------------------------
    def _compute_obs(self, reset_ids: np.ndarray) -> TensorDict:
        cfg, N, nz = self.cfg, self.num_envs, self.cfg.noise
        st = self.state
        _, _, _, lin_b, ang_b, _, grav, _ = self._base_quantities()
        q = st[:, self.q0 + self.obs_qadr]
        qd = st[:, self.v0 + self.obs_vadr]
        u = lambda shape, a: self.rng.uniform(-a, a, shape)
        grav_b = grav.copy()
        grav_b[:, 0] += self.imu_bias
        frame = {
            "base_ang_vel": (ang_b + u((N, 3), nz["ang_vel"])) * S.ANG_VEL_SCALE,
            "projected_gravity": grav_b + u((N, 3), nz["gravity"]),
            "velocity_commands": self.cmd,
            "joint_pos_rel": q - self.default_obs + u(q.shape, nz["joint_pos"]),
            "joint_vel_rel": (qd + u(qd.shape, nz["joint_vel"])) * S.JOINT_VEL_SCALE,
            "last_action": self.actions,
        }
        clean = {
            "base_ang_vel": ang_b * S.ANG_VEL_SCALE, "projected_gravity": grav,
            "velocity_commands": self.cmd, "joint_pos_rel": q - self.default_obs,
            "joint_vel_rel": qd * S.JOINT_VEL_SCALE, "last_action": self.actions,
        }
        for name, _ in S.OBS_TERMS:
            h = self.hist[name]
            h[:, :-1] = h[:, 1:]
            h[:, -1] = frame[name]
            if len(reset_ids):
                h[reset_ids] = frame[name][reset_ids][:, None, :]
        if self.teacher is not None:
            self.teacher_act = self.teacher.act(q, qd, ang_b, grav, self.cmd)
        actor = np.concatenate([self.hist[name].reshape(N, -1) for name, _ in S.OBS_TERMS], -1)
        critic_frame = np.concatenate([lin_b, clean["base_ang_vel"], clean["projected_gravity"], self.cmd,
                                       clean["joint_pos_rel"], clean["joint_vel_rel"], self.actions,
                                       self.contact.astype(float), self.free_goal - self.default_free,
                                       [[self.arm_scale]] * np.ones((N, 1))], -1)
        if self.crit_hist is None:
            self.crit_hist = np.repeat(critic_frame[:, None, :], S.HISTORY, 1)
        self.crit_hist[:, :-1] = self.crit_hist[:, 1:]
        self.crit_hist[:, -1] = critic_frame
        if len(reset_ids):
            self.crit_hist[reset_ids] = critic_frame[reset_ids][:, None, :]
        critic = self.crit_hist.reshape(N, -1)
        actor = np.clip(actor, -S.OBS_CLIP, S.OBS_CLIP)
        return TensorDict({
            "policy": torch.as_tensor(actor, dtype=torch.float32, device=self.device),
            "critic": torch.as_tensor(critic, dtype=torch.float32, device=self.device),
        }, batch_size=[N], device=self.device)

    def close(self) -> None:
        self.roller.close()
