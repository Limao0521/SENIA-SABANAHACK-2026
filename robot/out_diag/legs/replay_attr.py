"""Attribution replays: standing robot in CLUB decor, stream one SONIC turn under variants.

Conditions: (a) as-is SONIC, (b) GMR desired arms, (c) low-pass+rate-limited SONIC arms,
(d) waist delta=0, (e) head frozen, (f) arms frozen at rest, (g) all frozen (baseline),
(h) GMR waist instead of SONIC waist.
Leg-reaction metrics: leg qvel RMS, base horizontal speed, xy drift, tilt, pelvis-z std.
"""
import os, sys, json, time
os.environ.setdefault("MUJOCO_GL", "egl")
import numpy as np

sys.path.insert(0, "/home/luis/GitHub/x2_ultra-reto2/src")
from x2_ultra.show.engine import ShowEngine
from x2_ultra.show.stage import CLUB
from x2_ultra.robot import joints as J
from x2_ultra.show.hri.motion import SonicClip, REST_ARMS, WAIST_LIMIT
from x2_ultra.control import obs_spec as S

TURN = sys.argv[1] if len(sys.argv) > 1 else "rn_luis_t02_dialogue"
NPZ = f"/home/luis/GitHub/x2_ultra-reto2/assets/hri/HRIEGO_gesture/opt/realnames/visits/luis/sonic/{TURN}/{TURN}_arms.npz"
PRE_S, DT = 2.0, S.POLICY_DT

z = np.load(NPZ)
t = z["t"]; cmd = z["cmd"]; gmr = z["gmr_desired"]; wh = z["waist_head"]; whg = z["waist_head_gmr"]
N = len(t)

def lp_rate(a, fc=1.0, rate=1.0, dt=0.02):
    """1-pole low-pass then slew-rate limit the streamed target."""
    al = 2 * np.pi * fc * dt / (1 + 2 * np.pi * fc * dt)
    out = np.empty_like(a); y = a[0].copy()
    for i, x in enumerate(a):
        y = y + al * (x - y); out[i] = y
    for i in range(1, len(out)):
        out[i] = out[i-1] + np.clip(out[i] - out[i-1], -rate*dt, rate*dt)
    return out

cmd_f = lp_rate(cmd)

CONDS = {
    "a_asis":      dict(arms=cmd,     waist=np.clip(wh[:, :3], -WAIST_LIMIT, WAIST_LIMIT), head=wh[:, 3:5]),
    "b_gmr_arms":  dict(arms=gmr,     waist=np.clip(wh[:, :3], -WAIST_LIMIT, WAIST_LIMIT), head=wh[:, 3:5]),
    "c_filt_arms": dict(arms=cmd_f,   waist=np.clip(wh[:, :3], -WAIST_LIMIT, WAIST_LIMIT), head=wh[:, 3:5]),
    "d_no_waist":  dict(arms=cmd,     waist=np.zeros((N, 3)),                              head=wh[:, 3:5]),
    "e_no_head":   dict(arms=cmd,     waist=np.clip(wh[:, :3], -WAIST_LIMIT, WAIST_LIMIT), head=np.zeros((N, 2))),
    "f_arms_rest": dict(arms=np.tile(REST_ARMS, (N, 1)), waist=np.clip(wh[:, :3], -WAIST_LIMIT, WAIST_LIMIT), head=wh[:, 3:5]),
    "g_frozen":    dict(arms=np.tile(REST_ARMS, (N, 1)), waist=np.zeros((N, 3)),            head=np.zeros((N, 2))),
    "h_gmr_waist": dict(arms=cmd,     waist=np.clip(whg[:, :3], -WAIST_LIMIT, WAIST_LIMIT), head=whg[:, 3:5]),
}

print("building engine...", flush=True)
eng = ShowEngine(CLUB)
eng.guest = None            # aislar: solo robot + comandos de upper body
rig = eng.rigs["ultra"]
import mujoco as _mj
PFX = rig.robot.prefix
LEG = [n for n in J.BODY_JOINTS if any(k in n for k in ("hip", "knee", "ankle"))]
_leg_ids = np.array([_mj.mj_name2id(eng.model, _mj.mjtObj.mjOBJ_JOINT, PFX + n) for n in LEG])
assert (_leg_ids >= 0).all(), f"leg joints not found with prefix {PFX!r}: {_leg_ids}"
vadr_leg = eng.model.jnt_dofadr[_leg_ids]
d = eng.data
res = {}
for cname, c in CONDS.items():
    eng.reset()
    rig.set_arm_targets(REST_ARMS); rig.set_waist_delta(np.zeros(3)); rig.set_head_targets(0, 0)
    for _ in range(int(PRE_S / DT)):
        eng.step_control()
    base0 = d.qpos[rig.qbase:rig.qbase+3].copy()
    rows = []
    t0 = time.perf_counter()
    for i in range(N):
        rig.set_arm_targets(c["arms"][i])
        rig.set_waist_delta(c["waist"][i])
        rig.set_head_targets(float(c["head"][i][0]), float(c["head"][i][1]))
        eng.step_control()
        q = d.qpos[rig.qbase+3:rig.qbase+7]
        up_z = 1 - 2*(q[1]**2 + q[2]**2)
        tilt = np.degrees(np.arccos(np.clip(up_z, -1, 1)))
        v = d.qvel[rig.vbase:rig.vbase+6]
        rows.append([np.sqrt(np.mean(d.qvel[vadr_leg]**2)), np.hypot(v[0], v[1]),
                     np.hypot(*(d.qpos[rig.qbase:rig.qbase+2]-base0[:2])), tilt,
                     d.qpos[rig.qbase+2], np.abs(v[2]), abs(v[3])+abs(v[4])])
    wall = time.perf_counter() - t0
    R = np.array(rows)
    res[cname] = dict(leg_qvel_rms=float(np.sqrt(np.mean(R[:,0]**2))), leg_qvel_p95=float(np.percentile(R[:,0],95)),
                      base_hspeed_mean=float(R[:,1].mean()), base_hspeed_p95=float(np.percentile(R[:,1],95)),
                      xy_drift_end_m=float(R[-1,2]), xy_drift_max_m=float(R[:,2].max()),
                      tilt_mean_deg=float(R[:,3].mean()), tilt_p95_deg=float(np.percentile(R[:,3],95)), tilt_max_deg=float(R[:,3].max()),
                      pelvis_z_std_mm=float(R[:,4].std()*1000), base_wz_p95=float(np.percentile(R[:,5],95)),
                      angvel_xy_p95=float(np.percentile(R[:,6],95)), wall_s=round(wall,1))
    print(f"{cname:12s} {json.dumps(res[cname])}", flush=True)

json.dump({"turn": TURN, "N": N, "results": res}, open(f"/home/luis/GitHub/x2_ultra-reto2/out_diag/legs/replay_{TURN}.json", "w"), indent=1)
print("done")
