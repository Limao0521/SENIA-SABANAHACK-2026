"""L2: waist-bounds / arm-amplitude experiments on top of current W1 code (read-only import).

Conditions per turn:
  w1      : current W1 code as-is (bounds .18/.08/.05, arms SONIC as-is)
  b10     : bounds yaw .10, pitch .05, roll .03
  b08     : bounds yaw .08, pitch .05, roll .03
  k07     : arms = REST + 0.7*(cmd-REST)
  k06     : arms = REST + 0.6*(cmd-REST)
  c_b10k07: b10 + k07
  c_b08k06: b08 + k06
Metrics: legs qvel RMS (standing), tilt mean/max, xy drift, waist corr vs LP-1.5Hz SONIC,
arm RMS displacement (ratio vs w1), peak arm speed, % ticks arm speed > 0.35 rad/s.
Writes out_diag/legs2/exp_<turn>.json. No tracked/src file is modified (monkeypatch only).
"""
import os, sys, json, time
os.environ.setdefault("MUJOCO_GL", "egl")
import numpy as np

sys.path.insert(0, "/home/luis/GitHub/x2_ultra-reto2/src")
from x2_ultra.show import engine as E
from x2_ultra.show.engine import ShowEngine
from x2_ultra.show.stage import CLUB
from x2_ultra.robot import joints as J
from x2_ultra.show.hri.motion import REST_ARMS, WAIST_LIMIT
from x2_ultra.control import obs_spec as S

TURNS = [("luis", "ps_luis_t02_dialogue"), ("laura", "ps_laura_t03_dialogue"),
         ("sebastian", "ps_sebastian_t04_dialogue")]
PRE_S, DT = 2.0, S.POLICY_DT
B_DEF = np.array([0.18, 0.08, 0.05])
B10 = np.array([0.10, 0.05, 0.03])
B08 = np.array([0.08, 0.05, 0.03])

def scale(arms, k):
    return REST_ARMS + k * (arms - REST_ARMS)

CONDS = {
    "w1":       dict(b=B_DEF, k=1.0),
    "b10":      dict(b=B10,   k=1.0),
    "b08":      dict(b=B08,   k=1.0),
    "k07":      dict(b=B_DEF, k=0.7),
    "k06":      dict(b=B_DEF, k=0.6),
    "c_b10k07": dict(b=B10,   k=0.7),
    "c_b08k06": dict(b=B08,   k=0.6),
}

print("building engine...", flush=True)
eng = ShowEngine(CLUB)
eng.guest = None
rig = eng.rigs["ultra"]
import mujoco as _mj
PFX = rig.robot.prefix
LEG = [n for n in J.BODY_JOINTS if any(s in n for s in ("hip", "knee", "ankle"))]
def dofs(names):
    ids = np.array([_mj.mj_name2id(eng.model, _mj.mjtObj.mjOBJ_JOINT, PFX + n) for n in names])
    return eng.model.jnt_qposadr[ids], eng.model.jnt_dofadr[ids]
leg_qadr, leg_vadr = dofs(LEG)
arm_qadr, arm_vadr = dofs(list(J.ARM_JOINTS))
d = eng.data
out = {}
for person, turn in TURNS:
    NPZ = (f"/home/luis/GitHub/x2_ultra-reto2/assets/hri/HRIEGO_gesture/opt/realnames/"
           f"visits/{person}/sonic/{turn}/{turn}_arms.npz")
    z = np.load(NPZ)
    t = z["t"]; cmd = z["cmd"]; wh = z["waist_head"]
    N = len(t)
    waist_in = np.clip(wh[:, :3], -WAIST_LIMIT, WAIST_LIMIT)
    head_in = wh[:, 3:5]
    # ideal LP 1.5 Hz of raw SONIC waist (tracking reference)
    al = 2 * np.pi * 1.5 * DT / (1 + 2 * np.pi * 1.5 * DT)
    ideal = np.empty_like(waist_in); y = waist_in[0].copy()
    for i, x in enumerate(waist_in):
        y = y + al * (x - y); ideal[i] = y
    out[turn] = {}
    for cname, c in CONDS.items():
        rig._wf.bounds[:] = c["b"]
        arms = cmd if c["k"] == 1.0 else scale(cmd, c["k"])
        eng.reset()
        rig.set_arm_targets(REST_ARMS); rig.set_waist_delta(np.zeros(3)); rig.set_head_targets(0, 0)
        for _ in range(int(PRE_S / DT)):
            eng.step_control()
        base0 = d.qpos[rig.qbase:rig.qbase + 3].copy()
        rows = []; wrows = []; arows = []; varms = []
        t0 = time.perf_counter()
        for i in range(N):
            rig.set_arm_targets(arms[i])
            rig.set_waist_delta(waist_in[i])
            rig.set_head_targets(float(head_in[i][0]), float(head_in[i][1]))
            eng.step_control()
            q = d.qpos[rig.qbase + 3:rig.qbase + 7]
            up_z = 1 - 2 * (q[1] ** 2 + q[2] ** 2)
            tilt = np.degrees(np.arccos(np.clip(up_z, -1, 1)))
            v = d.qvel[rig.vbase:rig.vbase + 6]
            rows.append([np.sqrt(np.mean(d.qvel[leg_vadr] ** 2)), tilt,
                         np.hypot(*(d.qpos[rig.qbase:rig.qbase + 2] - base0[:2]))])
            wrows.append(rig.waist_delta.copy())
            arows.append(d.qpos[arm_qadr].copy())
            varms.append(np.abs(d.qvel[arm_vadr]).max())
        wall = time.perf_counter() - t0
        R = np.array(rows); W = np.array(wrows); A = np.array(arows); VA = np.array(varms)
        arm_rms = float(np.sqrt(np.mean((A - REST_ARMS) ** 2)))
        corr = [float(np.corrcoef(W[:, k], ideal[:, k])[0, 1]) for k in range(3)]
        out[turn][cname] = dict(
            leg_qvel_rms=float(np.sqrt(np.mean(R[:, 0] ** 2))),
            tilt_mean_deg=float(R[:, 1].mean()), tilt_max_deg=float(R[:, 1].max()),
            xy_drift_max_m=float(R[:, 2].max()), xy_drift_end_m=float(R[-1, 2]),
            waist_corr_ypr=corr, waist_corr_min=min(corr),
            arm_rms_disp=arm_rms, arm_peak_speed=float(VA.max()),
            arm_pct_ticks_gt035=float((VA > 0.35).mean() * 100), wall_s=round(wall, 1))
        print(f"{turn} {cname:9s} {json.dumps(out[turn][cname])}", flush=True)
    rig._wf.bounds[:] = B_DEF
    json.dump(out, open(f"/home/luis/GitHub/x2_ultra-reto2/out_diag/legs2/exp_all.json", "w"), indent=1)
print("done")
