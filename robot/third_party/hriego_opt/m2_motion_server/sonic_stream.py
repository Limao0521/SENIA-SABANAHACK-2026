"""Online SONIC for the X2 (inside the physicalai process): ONNX policy + MuJoCo (.mjb) stepped at 50 Hz on a reference that is still being produced.
Ports eval_x2_mujoco_onnx.step_once (deploy tuning preset included) and x2_sonic.convert_one's entry prefix; no pickle/CSV/trace, the only
state is a bounded ring of recent reference frames. Emits the *actual* arm/waist/head joints every tick (what prep_arms extracts)."""
import os, sys, time
import numpy as np
from m2common import *

S = {}
LOOK = 9 * 0.1                      # SONIC reads reference t .. t+0.9 s (10 frames, 0.1 s apart)
ARM_PARTS = ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll")
ARM = [f"{s}_{p}_joint" for s in ("left", "right") for p in ARM_PARTS]
WH = ["waist_yaw_joint", "waist_pitch_joint", "waist_roll_joint", "head_yaw_joint", "head_pitch_joint"]


def load(mjb_name="sonic_x2_ultra.mjb", ort_threads=2):
    os.environ.setdefault("MUJOCO_GL", "egl"); os.environ.setdefault("ORT_NUM_THREADS", str(ort_threads))
    import mujoco
    sys.path.insert(0, str(P.MOTION_DIR)); import x2_sonic as X
    sys.path.insert(0, str(X.SONIC_SCRIPTS)); sys.path.insert(0, str(X.RETARGETER))
    import eval_x2_mujoco_onnx as runner, eval_x2_mujoco as E
    from retarget_quality import foot_collision_geoms, sole_height
    actor = runner.OnnxActor(str(X.SONIC_ROOT / "models/x2_sonic_14000_g1.onnx"))
    model = mujoco.MjModel.from_binary_path(str(MJB_DIR / mjb_name)); model.opt.timestep = E.SIM_DT
    tuning = E.load_deploy_tuning(str(X.SONIC_ROOT / "configs/real_deploy_tuning/bigrun.yaml"))
    S.update(X=X, runner=runner, E=E, mujoco=mujoco, actor=actor, model=model, data=mujoco.MjData(model), tuning=tuning,
             feet=foot_collision_geoms(model, mujoco), sole_height=sole_height,
             arm_idx=[E.MUJOCO_JOINT_NAMES.index(n) for n in ARM], wh_idx=[E.MUJOCO_JOINT_NAMES.index(n) for n in WH])
    mujoco.mj_setCacheCapacity(mujoco.mj_getCache(), 0)
    trim()


def _quat_to_mat(q_xyzw):
    x, y, z, w = q_xyzw
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _slerp(q0, q1, a):
    d = float(np.dot(q0, q1))
    if d < 0: q1 = -q1; d = -d
    if d > 0.9995:
        q = q0 + a * (q1 - q0); return q / np.linalg.norm(q)
    th = np.arccos(d); return (np.sin((1 - a) * th) * q0 + np.sin(a * th) * q1) / np.sin(th)


class Resampler:
    """GMR frames at `rate` Hz -> 50 Hz (linear joints/pos, slerp quaternion), incremental; same grid/clamp as x2_sonic.resample."""
    def __init__(self, rate): self.rate = float(rate); self.q = []; self.j = 0; self.base = 0
    def push(self, q):
        self.q.append(q); out = []
        while True:
            pos = self.j * self.rate / 50.0; i0 = int(np.floor(pos + 1e-9)); a = pos - i0
            if i0 - self.base >= len(self.q): break
            if a < 1e-9: out.append(self.q[i0 - self.base].copy())
            elif i0 + 1 - self.base < len(self.q):
                q0, q1 = self.q[i0 - self.base], self.q[i0 + 1 - self.base]; r = (1 - a) * q0 + a * q1
                r[3:7] = _slerp(q0[3:7], q1[3:7], a); out.append(r)
            else: break
            self.j += 1
        drop = int(np.floor(self.j * self.rate / 50.0)) - 1 - self.base
        if drop > 0: del self.q[:drop]; self.base += drop
        return out
    def flush(self):
        n = self.base + len(self.q); total = int(round((n - 1) / self.rate * 50.0)) + 1; out = []
        while self.j < total:
            pos = min(self.j / 50.0 * self.rate, n - 1); i0 = int(np.floor(pos + 1e-9)); a = pos - i0
            q0 = self.q[min(i0, n - 1) - self.base]
            if a < 1e-9 or i0 + 1 > n - 1: out.append(q0.copy())
            else:
                q1 = self.q[i0 + 1 - self.base]; r = (1 - a) * q0 + a * q1; r[3:7] = _slerp(q0[3:7], q1[3:7], a); out.append(r)
            self.j += 1
        return out


class SonicOnline:
    def __init__(self, hold_n=25, blend_s=1.5):
        self.hold_n, self.blend_n = hold_n, int(round(blend_s * 50)); self.E = S["E"]; self.mj = S["mujoco"]
        self.model, self.data, self.actor, self.tuning = S["model"], S["data"], S["actor"], S["tuning"]
        E = self.E; self.kp, self.kd = self.tuning["kp"], self.tuning["kd"]
        self.prop = E.ProprioceptionBuffer(); self.last_action = np.zeros(E.NUM_DOFS, np.float32); self.lpf = None
        self.dof = []; self.rot = []; self.base = 0          # reference ring: 50 Hz dof (31), root rotation matrices (3,3)
        self.sim_time = 0.0; self.step = 0; self.started = False; self.n_ref_total = None; self.fell = False
        self.prefix_n = hold_n + self.blend_n

    # ---- reference construction -------------------------------------------------------------------------------------
    def _append(self, dof, mat): self.dof.append(dof); self.rot.append(mat)

    def start(self, q0):
        """First real 50 Hz GMR frame known: build hold + smoothstep blend prefix (x2_sonic.convert_one), RSI-init the sim."""
        E, mj = self.E, self.mj
        start = q0.copy(); start[7:] = E.DEFAULT_DOF
        self.data.qpos[:] = start; mj.mj_forward(self.model, self.data); start[2] -= S["sole_height"](self.model, self.data, S["feet"])
        u = np.linspace(0.0, 1.0, self.blend_n + 1)[1:]; w = 3 * u ** 2 - 2 * u ** 3
        mat0 = _quat_to_mat(np.array([q0[4], q0[5], q0[6], q0[3]]))
        for _ in range(self.hold_n): self._append(start[7:].copy(), _quat_to_mat(np.array([start[4], start[5], start[6], start[3]])))
        for wi in w: self._append(start[7:] + wi * (q0[7:] - start[7:]), mat0)
        # RSI init (compute_motion_state at frame 0): velocities are zero when the first two prefix frames are equal (hold_n >= 2)
        d = self.data; d.qpos[0] = 0.0; d.qpos[1] = 0.0; d.qpos[2] = start[2]; d.qpos[3:7] = start[3:7]; d.qpos[7:7 + E.NUM_DOFS] = start[7:]
        d.qvel[:] = 0; d.xfrc_applied[:] = 0
        if self.hold_n < 2: d.qvel[6:6 + E.NUM_DOFS] = (self.dof[1] - self.dof[0]) * 50.0
        mj.mj_forward(self.model, d); self.started = True

    def push_ref(self, q):
        self._append(q[7:].copy(), _quat_to_mat(np.array([q[4], q[5], q[6], q[3]])))

    def end_ref(self): self.n_ref_total = self.base + len(self.dof)

    def n_ref(self): return self.base + len(self.dof)

    def can_tick(self):
        if not self.started: return False
        mf = self._mf()
        need = int((mf / 50.0 + 9 * self.E.DT_FUTURE_REF) / 0.02)          # highest future frame index read by the tokenizer
        if self.n_ref_total is not None: return self.step < self.n_ref_total
        return self.n_ref() >= need + 1

    def done(self): return self.n_ref_total is not None and self._mf() >= self.n_ref_total

    def _mf(self): return int(self.sim_time * 50.0)

    def _tokenizer(self, mf, base_quat):
        E = self.E; dt = 1.0 / 50.0; ct = mf / 50.0; total = self.n_ref_total if self.n_ref_total is not None else 10 ** 9
        Rc = _quat_to_mat(np.array([base_quat[1], base_quat[2], base_quat[3], base_quat[0]])).T
        jp, jv, ori = [], [], []
        for f in range(E.NUM_FUTURE_FRAMES):
            fi = min(int((ct + f * E.DT_FUTURE_REF) / dt), total - 1); pf = max(0, fi - 1)
            dfi = self.dof[fi - self.base]; jp.append(dfi[E.IL_TO_MJ_DOF].astype(np.float32))
            jv.append(((dfi - self.dof[pf - self.base]) * 50.0)[E.IL_TO_MJ_DOF].astype(np.float32))
            ori.append((Rc @ self.rot[fi - self.base])[:, :2].reshape(-1).astype(np.float32))
        cmd = np.concatenate([np.concatenate(jp), np.concatenate(jv)]).reshape(E.NUM_FUTURE_FRAMES, -1)
        return np.concatenate([cmd, np.stack(ori)], axis=-1).reshape(-1).astype(np.float32)

    # ---- one 50 Hz control tick (eval_x2_mujoco_onnx.step_once) ---------------------------------------------------------
    def tick(self):
        E, mj, d = self.E, self.mj, self.data; mf = self._mf()
        qpos_j = d.qpos[7:7 + E.NUM_DOFS].copy(); qvel_j = d.qvel[6:6 + E.NUM_DOFS].copy()
        base_quat = d.qpos[3:7].copy(); base_angvel = d.qvel[3:6].copy()
        dof_pos_il = qpos_j[E.IL_TO_MJ_DOF]; dof_vel_il = qvel_j[E.IL_TO_MJ_DOF]; action_il = self.last_action[E.IL_TO_MJ_DOF]
        gravity = E.quat_rotate_inverse(base_quat, np.array([0.0, 0.0, -1.0]))
        self.prop.append(gravity, base_angvel, dof_pos_il - E.DEFAULT_DOF[E.IL_TO_MJ_DOF], dof_vel_il, action_il)
        a_il = self.actor(self.prop.get_flat(), self._tokenizer(mf, base_quat))
        action = np.clip(a_il[E.MJ_TO_IL_DOF], -self.tuning["action_clip"], self.tuning["action_clip"])
        self.last_action = action.astype(np.float32).copy()
        target = E.DEFAULT_DOF + action * E.ACTION_SCALE
        target = np.clip(target, E.DEFAULT_DOF - self.tuning["max_target_dev"], E.DEFAULT_DOF + self.tuning["max_target_dev"])
        if self.lpf is None: self.lpf = target.copy()
        else: self.lpf += self.tuning["lpf_alpha"] * (target - self.lpf)
        target = self.lpf.copy()
        for _ in range(E.DECIMATION):
            torque = self.kp * (target - d.qpos[7:7 + E.NUM_DOFS]) - self.kd * d.qvel[6:6 + E.NUM_DOFS]
            for j in range(E.NUM_DOFS): d.ctrl[E.JOINT_TO_ACTUATOR[j]] = torque[j]
            mj.mj_step(self.model, d)
        self.sim_time += E.CONTROL_DT; self.step += 1
        # trim reference frames that can no longer be read (>= 1 frame behind the current one)
        drop = mf - 2 - self.base
        if drop > 50: del self.dof[:drop]; del self.rot[:drop]; self.base += drop
        q = d.qpos[7:7 + E.NUM_DOFS]; g = E.quat_rotate_inverse(d.qpos[3:7], np.array([0.0, 0.0, -1.0]))
        tilt = float(np.rad2deg(np.arccos(np.clip(-g[2], -1.0, 1.0))))
        if d.qpos[2] < 0.4 or g[2] > -0.3: self.fell = True
        return mf, q[S["arm_idx"]].copy(), q[S["wh_idx"]].copy(), float(d.qpos[2]), tilt
