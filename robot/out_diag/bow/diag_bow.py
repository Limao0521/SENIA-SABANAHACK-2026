"""TASK D2: replay headless de la venia VIP (GREETING_VIP 3001/11 = NM_bow.csv) con logging 50 Hz.

Uso: MUJOCO_GL=egl python out_diag/bow/diag_bow.py <fix> <out.csv>
fix: baseline | a_slow_waist | b_arms_fwd | e_no_waist | ab_combo
"""
import os, sys, json
os.environ.setdefault("MUJOCO_GL", "egl")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
import numpy as np
import mujoco
from x2_ultra.show.stage import CLUB
from x2_ultra.show.engine import ShowEngine
from x2_ultra.show.hri.data import VisitStore, use_person_visits
from x2_ultra.show.hri.player import plan_turn, HriPlayer
from x2_ultra.show.hri.motion import D_ARMS
from x2_ultra.robot import joints as J

FIX = sys.argv[1] if len(sys.argv) > 1 else "baseline"
OUT = sys.argv[2] if len(sys.argv) > 2 else f"out_diag/bow/{FIX}.csv"
SIM_SECONDS = 26.0

use_person_visits("Laura")
store = VisitStore()
turns = store.visit_plan("vip")
greet = next(t for t in turns if t.emote == "GREETING_VIP")
plans = [plan_turn(greet, store)]
print(f"csv={store.csv_for('GREETING_VIP')} audio_start={plans[0].audio_start:.2f} "
      f"csv_start={plans[0].csv_start:.2f} csv_end={plans[0].csv_end:.2f} end={plans[0].end:.2f}")

eng = ShowEngine(CLUB)
rig = eng.rigs["ultra"]
d = eng.data
pre = rig.robot.prefix

# --- monkeypatch del fix (nada de ediciones a archivos trackeados) ---
WAIST_CAP = np.array([0.35, 0.15, 0.15])
SLEW = None
if FIX == "a_slow_waist":
    WAIST_CAP = np.array([0.35, 0.10, 0.10]); SLEW = 0.06   # rad/s: rampa lenta (min-jerk-ish)
elif FIX == "ab_combo":
    WAIST_CAP = np.array([0.35, 0.10, 0.10]); SLEW = 0.06
_orig_sample = plans[0].preset.sample if plans[0].preset is not None else None
if FIX in ("b_arms_fwd", "ab_combo") and _orig_sample is not None:
    def sample_noarms(t, _s=_orig_sample):
        a, w, h = _s(t)
        return D_ARMS.copy(), w, h          # brazos al frente (D), sin swing atrás
    plans[0].preset.sample = sample_noarms

wd_target = np.zeros(3)
wd_cur = np.zeros(3)
if FIX in ("a_slow_waist", "b_arms_fwd", "ab_combo", "e_no_waist"):
    def set_wd(q3, _rig=rig):
        global wd_target
        wd_target = np.clip(np.asarray(q3, float).reshape(-1)[:3], -WAIST_CAP, WAIST_CAP)
    rig.set_waist_delta = set_wd
    if FIX == "e_no_waist":
        WAIST_CAP = np.zeros(3)

# foot geoms para el polígono de soporte
foot_bodies = {mujoco.mj_name2id(eng.model, mujoco.mjtObj.mjOBJ_BODY, pre + s + "_ankle_roll_link")
               for s in ("left", "right")}
foot_geoms = {g for g in range(eng.model.ngeom) if eng.model.geom_bodyid[g] in foot_bodies}
print("foot geoms:", [mujoco.mj_id2name(eng.model, mujoco.mjtObj.mjOBJ_GEOM, g) for g in foot_geoms])

jid = lambda n: mujoco.mj_name2id(eng.model, mujoco.mjtObj.mjOBJ_JOINT, pre + n)
root_body = eng.model.jnt_bodyid[jid("floating_base_joint")]   # COM del robot = subtree de su raíz
qadr = {n: eng.model.jnt_qposadr[jid(n)] for n in
        ("waist_pitch_joint", "left_hip_pitch_joint", "left_knee_joint", "left_ankle_pitch_joint",
         "right_hip_pitch_joint", "right_knee_joint", "right_ankle_pitch_joint",
         "left_shoulder_pitch_joint", "right_shoulder_pitch_joint")}

player = HriPlayer("ultra", plans, audio_fn=None)
rows = []
eng.run_for(1.0)
x0 = d.qpos[rig.qbase]
t_gesture = [plans[0].t0 + plans[0].csv_start if not np.isnan(plans[0].t0) else np.nan,
             plans[0].t0 + plans[0].csv_end if not np.isnan(plans[0].t0) else np.nan]

while d.time < SIM_SECONDS:
    player.tick(d.time, rig)
    if FIX in ("a_slow_waist", "b_arms_fwd", "ab_combo", "e_no_waist"):
        step = SLEW * 0.02 if SLEW else 1e9
        wd_cur = wd_cur + np.clip(wd_target - wd_cur, -step, step)
        rig.waist_delta = wd_cur.copy()
    eng.step_control()
    if not np.isnan(plans[0].t0) and np.isnan(t_gesture[0]):
        t_gesture = [plans[0].t0 + plans[0].csv_start, plans[0].t0 + plans[0].csv_end]
    com = d.subtree_com[root_body].copy()
    sup = [d.contact[i].pos for i in range(d.ncon)
           if d.contact[i].geom1 in foot_geoms or d.contact[i].geom2 in foot_geoms]
    sup_xmin = min((p[0] for p in sup), default=np.nan)
    sup_xmax = max((p[0] for p in sup), default=np.nan)
    q = d.qpos[rig.qbase + 3:rig.qbase + 7]
    pitch = np.arcsin(np.clip(2 * (q[3] * q[1] - q[0] * q[2]), -1, 1))   # tilt adelante-ish (x)
    roll = np.arctan2(2 * (q[3] * q[2] + q[0] * q[1]), 1 - 2 * (q[1] ** 2 + q[2] ** 2))
    rows.append([round(d.time, 3),
                 d.qpos[rig.qbase], d.qpos[rig.qbase + 1], d.qpos[rig.qbase + 2],
                 d.qvel[rig.vbase], d.qvel[rig.vbase + 1],
                 com[0], com[1], com[2], sup_xmin, sup_xmax,
                 np.degrees(pitch), np.degrees(roll),
                 d.ctrl[rig.act_waist[1]], d.qpos[qadr["waist_pitch_joint"]], rig.waist_delta[1],
                 d.qpos[qadr["left_hip_pitch_joint"]], d.qpos[qadr["left_knee_joint"]],
                 d.qpos[qadr["left_ankle_pitch_joint"]], d.qpos[qadr["right_hip_pitch_joint"]],
                 d.ctrl[rig.act_policy].max(), d.ctrl[rig.act_policy].min(),
                 d.qpos[qadr["left_shoulder_pitch_joint"]], d.qpos[qadr["right_shoulder_pitch_joint"]],
                 int(player.current is not None and player.current.preset is not None)])
np.savetxt(OUT, np.array(rows), delimiter=",", fmt="%.5f",
           header="t,bx,by,bz,vx,vy,comx,comy,comz,supxmin,supxmax,tilt_x_deg,roll_deg,"
                  "waistp_ctrl,waistp_q,waistp_delta,lhip,lknee,lank,rhip,act_max,act_min,"
                  "lshp,rshp,gest", comments="")
x = np.array(rows)
g0, g1 = t_gesture
m = (x[:, 0] >= g0) & (x[:, 0] <= g1) if not np.isnan(g0) else np.zeros(len(x), bool)
res = {"fix": FIX, "x0": float(x0), "x_end": float(x[-1, 1]),
       "drift_total_m": float(x[-1, 1] - x0),
       "drift_during_bow_m": float(x[m][-1, 1] - x[m][0, 1]) if m.any() else None,
       "min_vx_during_bow": float(x[m, 4].min()) if m.any() else None,
       "com_margin_back_min_m": float(np.nanmin(x[:, 6] - x[:, 9])) if not np.isnan(x[0, 9]) else None,
       "max_tilt_deg": float(np.nanmax(np.abs(x[:, 11]))),
       "max_waistp_q": float(x[:, 14].max()), "fell": rig.state != "activo",
       "gesture_window": [float(g0), float(g1)]}
print(json.dumps(res))
json.dump(res, open(OUT.replace(".csv", "_summary.json"), "w"), indent=1)
