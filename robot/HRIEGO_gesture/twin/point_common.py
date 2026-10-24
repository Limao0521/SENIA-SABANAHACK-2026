"""4aa shared helpers: X2 twin-model FK, c04 angular-error definition, c03 pointing target poses."""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import numpy as np, mujoco
C03 = str(_LR / "experiments/RCP-01/runs/20261020_rcp01c03_segment_scheduler/motion/x2/retarget/")
VEC = {"RIGHT": np.array([0., -1., 0.]), "LEFT": np.array([0., 1., 0.]), "FRONT": np.array([1., 0., 0.])}   # X2_base_local
SIDE = {"RIGHT": "right", "LEFT": "left", "FRONT": "right"}
PARTS = ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll")

class FK:
    def __init__(self):
        from x2_ultra.twin.model import build_twin_model
        self.m = build_twin_model("vacia"); self.d = mujoco.MjData(self.m)
        g = lambda n: mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_BODY, n)
        self.ids = {s: (g(f"{s}_shoulder_pitch_link"), g(f"{s}_elbow_link"), g(f"{s}_wrist_roll_link")) for s in ("left", "right")}
        self.pelvis = g("pelvis")
        self.adr = {mujoco.mj_id2name(self.m, mujoco.mjtObj.mjOBJ_JOINT, j): self.m.jnt_qposadr[j] for j in range(self.m.njnt)}
    def arm_vec(self, qpos, side):
        """c04 (x2_sonic.py): wrist - shoulder_pitch_link in the root (pelvis) frame, + extension = |w-s| / chain length."""
        self.d.qpos[:] = qpos; mujoco.mj_forward(self.m, self.d)
        sh, el, wr = (self.d.xpos[i].copy() for i in self.ids[side])
        R = self.d.xmat[self.pelvis].reshape(3, 3)
        loc = R.T @ (wr - sh)
        ext = np.linalg.norm(wr - sh) / (np.linalg.norm(el - sh) + np.linalg.norm(wr - el))
        return loc, ext
    def err_deg(self, qpos, direction):
        loc, ext = self.arm_vec(qpos, SIDE[direction])
        return float(np.degrees(np.arccos(np.clip(loc @ VEC[direction] / np.linalg.norm(loc), -1, 1)))), float(ext)
    def qpos_with(self, arm14, base_qpos=None):
        q = (self.m.qpos0 if base_qpos is None else base_qpos).copy()
        for n, v in arm14.items(): q[self.adr[n]] = v
        return q
