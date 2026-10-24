"""SONIC MuJoCo trace -> 14 arm angles (AimDK order, rad, 50 Hz) for /mc/upper_body_command."""
import csv, json, sys
import numpy as np

SIDES, PARTS = ("left", "right"), ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow",
                                   "wrist_yaw", "wrist_pitch", "wrist_roll")
ARM = [f"{s}_{p}_joint" for s in SIDES for p in PARTS]
# URDF v1.4.0 limits, copied from x2_ultra/robot/joints.py JOINT_RANGE (teleop repo).
R = {"shoulder_pitch": (-3.0805, 2.0333), "shoulder_yaw": (-2.5569, 2.5569), "elbow": (-2.3562, 0.0),
     "wrist_yaw": (-2.5569, 2.5569), "wrist_pitch": (-0.5236, 0.5236)}
LIM = {}
for s in SIDES:
    for p in PARTS:
        if p == "shoulder_roll":
            lo, hi = (-0.0611, 3.0456) if s == "left" else (-3.0456, 0.0611)
        elif p == "wrist_roll":
            lo, hi = (-1.5097, 0.7243) if s == "left" else (-0.7243, 1.5097)
        else:
            lo, hi = R[p]
        LIM[f"{s}_{p}_joint"] = (lo + 0.05, hi - 0.05)   # LIMIT_MARGIN of control.py

trace, out = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(trace)))
t = np.array([float(r["time_s"]) for r in rows])
act = np.array([[float(r[f"{n}_actual_rad"]) for n in ARM] for r in rows])
des = np.array([[float(r[f"{n}_desired_rad"]) for n in ARM] for r in rows])
lo = np.array([LIM[n][0] for n in ARM]); hi = np.array([LIM[n][1] for n in ARM])
cmd = np.clip(act, lo, hi)
vel = np.diff(act, axis=0) / 0.02
WH = ["waist_yaw_joint", "waist_pitch_joint", "waist_roll_joint", "head_yaw_joint", "head_pitch_joint"]
wh = np.array([[float(r[f"{n}_actual_rad"]) for n in WH] for r in rows])
wh_des = np.array([[float(r[f"{n}_desired_rad"]) for n in WH] for r in rows])
np.savez(out, t=t, sonic_actual=act, waist_head=wh, waist_head_gmr=wh_des, waist_head_names=np.array(WH), gmr_desired=des, cmd=cmd, names=np.array(ARM),
         pelvis_z=np.array([float(r["pelvis_z_actual_m"]) for r in rows]),
         tilt=np.array([float(r["tilt_deg"]) for r in rows]))
print(json.dumps({
    "rows": len(rows), "duration_s": round(float(t[-1] - t[0]) + 0.02, 2),
    "clamped_samples": int((cmd != act).sum()),
    "max_clamp_rad": round(float(np.abs(cmd - act).max()), 4),
    "peak_speed_rad_s": {n: round(float(v), 2) for n, v in zip(ARM, np.abs(vel).max(0))},
    "sonic_vs_gmr_arm_mae_rad": round(float(np.abs(act - des).mean()), 4),
    "sonic_pelvis_z_min": round(float(min(float(r["pelvis_z_actual_m"]) for r in rows)), 3),
    "sonic_tilt_max_deg": round(float(max(float(r["tilt_deg"]) for r in rows)), 2)}, indent=1))
