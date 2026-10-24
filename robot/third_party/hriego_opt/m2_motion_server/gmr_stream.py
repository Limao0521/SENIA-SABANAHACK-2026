"""Streaming GMR for the X2 (inside the physicalai process). Lean .mjb IK model, mink + patched smplx from the gmr env (site_gmr symlinks).
A `GmrStream` is armed once per request (object + collision limits + READY-pose warm start), then fed SMPL-X blocks; it yields qpos frames at
`rate` Hz (30 = production, 15/10 = decimated source frames, 50 = human frames interpolated to 50 Hz). No ground tracking (the SONIC policy
does not observe root z)."""
import os, sys, time
import numpy as np
from m2common import *

sys.path.insert(0, str(SITE_GMR))        # mink + smplx(patched) first, rest of physicalai after
G = {}


def load(mjb_name="ik_lean_3.11.0.mjb"):
    import mujoco
    sys.path.insert(0, str(P.MOTION_DIR)); import x2_retarget as X
    audit = X.verify_assets()
    sys.path.insert(0, str(X.RETARGETER)); sys.path.insert(0, str(X.GMR_ROOT)); os.chdir(X.GMR_ROOT)
    import torch; torch.set_num_threads(1); torch.set_grad_enabled(False)
    _orig = mujoco.MjModel.from_xml_path
    mjb = str(MJB_DIR / mjb_name)
    def _fx(path, *a, **k):   # redirect the IK-model XML parse (1.8 GB transient) to the precompiled .mjb
        if str(path).endswith("x2_mocap_toe_proxy.xml"): return mujoco.MjModel.from_binary_path(mjb)
        return _orig(path, *a, **k)
    mujoco.MjModel.from_xml_path = staticmethod(_fx)
    import batch_retarget_x2 as batch
    from general_motion_retargeting.params import ROBOT_XML_DICT
    from general_motion_retargeting.utils.smpl import get_gvhmr_data_offline_fast
    ROBOT_XML_DICT["agibot_x2"] = X.IK_XML; batch.X2_XML = X.IK_XML
    so = sys.stdout; batch.worker_init(canonical=True, canonical_height=X.CANONICAL_HEIGHT_M); sys.stdout = so
    batch._worker["subj_models"]["neutral"] = batch._worker["canon"]      # share the canonical SMPL-X (identical npz)
    from general_motion_retargeting import GeneralMotionRetargeting
    G.update(X=X, batch=batch, fn=get_gvhmr_data_offline_fast, torch=torch, mujoco=mujoco, analytic=GeneralMotionRetargeting, native=batch._worker["GMR"], audit=audit)
    mujoco.mj_setCacheCapacity(mujoco.mj_getCache(), 0)
    trim()


def _make_rt(solver):
    """Same construction as batch.retarget_frames (fresh retargeter + collision-avoidance limit)."""
    batch = G["batch"]; mujoco = G["mujoco"]
    rt = (G["native"] if solver == "native" else G["analytic"])(actual_human_height=1.8, src_human="smplx", tgt_robot="agibot_x2", verbose=False)
    model = rt.model

    def body_geoms(names):
        geoms = []
        for name in names:
            body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
            geoms += [g for g in range(model.ngeom) if model.geom_bodyid[g] == body and model.geom_contype[g] != 0]
        return geoms
    arm_l = [f"left_{p}_link" for p in ("elbow", "wrist_yaw", "wrist_pitch", "wrist_roll")]
    arm_r = [f"right_{p}_link" for p in ("elbow", "wrist_yaw", "wrist_pitch", "wrist_roll")]
    lower = ["pelvis", "torso_link"] + [f"{s}_{p}_link" for s in ("left", "right") for p in ("hip_pitch", "hip_roll", "hip_yaw", "knee")]
    gl, gr, glow = body_geoms(arm_l), body_geoms(arm_r), body_geoms(lower)
    rt.ik_limits.append(batch._worker["CollisionAvoidanceLimit"](model, [(gl + gr, glow), (gl, gr)], minimum_distance_from_collisions=batch.X2_ARM_COLLISION_MARGIN))
    return rt


def _lerp_frames(f0, f1, a):
    """Interpolate two GMR human-frame dicts: position lerp, orientation nlerp (consecutive frames, sign-aligned)."""
    out = {}
    for k, (p0, q0) in f0.items():
        p1, q1 = f1[k]
        if np.dot(q0, q1) < 0: q1 = -q1
        q = (1 - a) * q0 + a * q1; q = q / np.linalg.norm(q)
        out[k] = ((1 - a) * p0 + a * p1, q)
    return out


class GmrStream:
    def __init__(self, solver="native", rate=30, betas=None, warm_iters=20):
        self.solver, self.rate, self.warm_iters = solver, rate, warm_iters
        self.batch = G["batch"]; self.torch = G["torch"]
        t = time.perf_counter(); self.rt = _make_rt(solver); self.t_arm = time.perf_counter() - t
        canon = self.batch._worker["canon"]; b16 = np.zeros(16, np.float32); b16[:10] = np.asarray(betas, np.float32).reshape(-1)[:10]
        h_s = self.batch._tpose_height(canon, self.torch.tensor(b16).view(1, -1), self.torch)
        self.scale = self.batch._worker["canon_h"] / h_s
        self.canon = canon; self.prev = None; self.prevq = None; self.count = 0; self.next_j = 0; self.armed_warm = False
        self.src_idx = 0; self.busy = 0.0

    def frames(self, poses, trans):
        """SMPL-X block (n,165),(n,3) at 30 fps -> list of GMR human-frame dicts (30 fps, Z-up)."""
        torch = self.torch; n = len(poses)
        with torch.no_grad():
            out = self.canon(betas=torch.zeros(1, 16), global_orient=torch.tensor(poses[:, :3]).float(), body_pose=torch.tensor(poses[:, 3:66]).float(),
                             transl=torch.tensor(trans * self.scale).float(), left_hand_pose=torch.zeros(n, 45), right_hand_pose=torch.zeros(n, 45),
                             jaw_pose=torch.zeros(n, 3), leye_pose=torch.zeros(n, 3), reye_pose=torch.zeros(n, 3), return_full_pose=True)
        sd = {"mocap_frame_rate": np.asarray(30.0), "pose_body": poses[:, 3:66]}
        fr, _ = G["fn"](sd, self.canon, out, tgt_fps=30)
        return fr

    def _solve(self, frame):
        from retarget_quality import limit_joint_velocity
        cur = self.rt.retarget(frame).copy()
        cur, clipped = limit_joint_velocity(self.prevq, cur, self.rate)
        if clipped: self.rt.configuration.update(cur)
        self.prevq = cur
        return cur.copy()

    def push_iter(self, poses, trans):
        """Feed a block; yields qpos frames (38,) at `rate` as soon as each IK solve is done (busy time accumulated in self.busy)."""
        t = time.perf_counter(); fr = self.frames(poses, trans)
        for f in fr:
            if self.prev is None:                # first frame of the request: warm start (production: 20 IK iterations on frame 0)
                for _ in range(self.warm_iters): self.rt.retarget({k: (v[0].copy(), v[1].copy()) for k, v in f.items()})
            outq = []
            if self.rate in (30, 15, 10):
                if self.src_idx % (30 // self.rate) == 0: outq.append(self._solve(f))
            elif self.rate == 50:
                if self.prev is not None:
                    while self.next_j * 0.6 <= self.src_idx and self.next_j * 0.6 > self.src_idx - 1 - 1e-9:   # 50 Hz grid points inside [i-1, i]/30 s
                        a = self.next_j * 0.6 - (self.src_idx - 1)
                        outq.append(self._solve(_lerp_frames(self.prev, f, a))); self.next_j += 1
                else:
                    outq.append(self._solve(f)); self.next_j = 1
            self.prev = f; self.src_idx += 1
            self.busy += time.perf_counter() - t
            for q in outq: yield q
            t = time.perf_counter()
        self.busy += time.perf_counter() - t

    def push(self, poses, trans): return list(self.push_iter(poses, trans))
