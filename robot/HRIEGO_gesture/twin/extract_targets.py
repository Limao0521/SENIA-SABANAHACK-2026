"""Pointing targets for the twin. Seed = 7 pointing-arm joints of the best c03 MOTOR_anchor_<DIR> frame (human-retargeted; its waist/root
differ from the twin, so its c04 error in the twin pelvis frame is 31-36 deg). Refined with a bounded damped least-squares IK on the twin model
(waist 0, URDF limits -0.05 margin, regularised toward the c03 seed): wrist_roll_link -> shoulder_pitch_link + dir * 0.98 * chain length (c04 build_motion definition)."""
import json, numpy as np
from scipy.optimize import least_squares
from point_common import *
from x2_ultra.robot.joints import ARM_JOINTS
fk = FK(); out = {}
LIM = {}
for s in ("left", "right"):
    pass
import mujoco
for D in VEC:
    z = np.load(C03 + f"MOTOR_anchor_{D}_x2_gmr_full.npz", allow_pickle=True)
    names = [str(n) for n in z["joint_names"]]; Q = z["qpos"]; side = SIDE[D]
    jn = [f"{side}_{p}_joint" for p in PARTS]
    best = None
    for i, q in enumerate(Q):
        arm = {n: q[7 + names.index(n)] for n in ARM_JOINTS}
        e, ext = fk.err_deg(fk.qpos_with(arm), D)
        if ext >= 0.85 and (best is None or e < best[1]): best = (i, e, ext, arm)
    i, e0, ext0, arm = best
    seed = np.array([arm[n] for n in jn])
    lo = np.array([fk.m.jnt_range[mujoco.mj_name2id(fk.m, mujoco.mjtObj.mjOBJ_JOINT, n)][0] + 0.05 for n in jn])
    hi = np.array([fk.m.jnt_range[mujoco.mj_name2id(fk.m, mujoco.mjtObj.mjOBJ_JOINT, n)][1] - 0.05 for n in jn])
    seed = np.clip(seed, lo + 1e-4, hi - 1e-4)
    sh_id, el_id, wr_id = fk.ids[side]
    def setq(x):
        return fk.qpos_with(dict(zip(jn, x)))
    q0 = setq(seed); fk.d.qpos[:] = q0; mujoco.mj_forward(fk.m, fk.d)
    L = 0.98 * (np.linalg.norm(fk.d.xpos[el_id] - fk.d.xpos[sh_id]) + np.linalg.norm(fk.d.xpos[wr_id] - fk.d.xpos[el_id]))
    R = fk.d.xmat[fk.pelvis].reshape(3, 3)
    def res(x):
        fk.d.qpos[:] = setq(x); mujoco.mj_forward(fk.m, fk.d)
        want = fk.d.xpos[sh_id] + R @ (VEC[D] * L)
        return np.r_[(fk.d.xpos[wr_id] - want) * 10.0, 0.05 * (x - seed)]
    sol = least_squares(res, seed, bounds=(lo, hi))
    x = sol.x; e1, ext1 = fk.err_deg(setq(x), D)
    out[D] = {"c03_frame": i, "c03_pose_err_deg_in_twin": e0, "err_deg": e1, "extension": ext1, "side": side,
              "joints": dict(zip(jn, map(float, x))), "c03_seed": dict(zip(jn, map(float, seed)))}
    print(D, "c03 frame", i, "seed err %.1f" % e0, "-> IK err %.2f ext %.2f" % (e1, ext1), np.round(x, 3))
json.dump(out, open("runs/4aa_pointing/motion/point_targets.json", "w"), indent=1)
