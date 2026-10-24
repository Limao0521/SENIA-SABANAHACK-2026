"""Upstream smoothness: SMPL-X vs GMR desired vs SONIC actual (arms), plus waist streams."""
import sys, json
import numpy as np
sys.path.insert(0, "/home/luis/GitHub/x2_ultra-reto2/src")

TURN = sys.argv[1] if len(sys.argv) > 1 else "rn_luis_t02_dialogue"
base = f"/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/realnames/visits/luis/sonic/{TURN}"
z = np.load(f"{base}/{TURN}_arms.npz")
dt = float(np.median(np.diff(z["t"])))

def stats(a, dt, label):
    v = np.diff(a, axis=0) / dt
    j = np.diff(v, axis=0) / dt
    # max single-frame jump (discontinuity detector)
    step = np.max(np.abs(np.diff(a, axis=0)), axis=1)
    big = np.where(step > 5 * np.median(step) + 1e-9)[0]
    return dict(label=label, vel_rms=float(np.sqrt((v**2).mean())), vel_max=float(np.abs(v).max()),
                jerk_rms=float(np.sqrt((j**2).mean())), jerk_max=float(np.abs(j).max()),
                max_frame_jump_rad=float(step.max()), n_jump_frames=int(len(big)),
                jump_frames=big[:12].tolist())

out = {"turn": TURN, "dt": dt, "N": int(len(z['t']))}
out["sonic_cmd"] = stats(z["cmd"], dt, "SONIC actual cmd (arms14)")
out["gmr_desired"] = stats(z["gmr_desired"], dt, "GMR desired (arms14)")
out["sonic_actual_key"] = stats(z["sonic_actual"], dt, "sonic_actual")
out["waist_sonic"] = stats(z["waist_head"][:, :3], dt, "waist SONIC")
out["waist_gmr"] = stats(z["waist_head_gmr"][:, :3], dt, "waist GMR")
out["head_sonic"] = stats(z["waist_head"][:, 3:5], dt, "head SONIC")

# first-frames behavior (0.5 s hold + blend at clip start?)
c = z["cmd"]
out["start_hold_frames"] = int(np.argmax(np.max(np.abs(np.diff(c, axis=0)), axis=1) > 1e-6))
# waist delta magnitude stats
w = np.clip(z["waist_head"][:, :3], -np.array([0.35,0.15,0.15]), np.array([0.35,0.15,0.15]))
out["waist_clip_frac"] = float(np.mean(np.abs(z["waist_head"][:, :3]) > np.array([0.35,0.15,0.15])))
out["waist_abs_p95"] = np.percentile(np.abs(w), 95, axis=0).tolist()
wg = z["waist_head_gmr"][:, :3]
out["waist_gmr_abs_p95"] = np.percentile(np.abs(wg), 95, axis=0).tolist()

# SMPL-X source if present
try:
    zs = np.load(f"{base}/smplx/{TURN}.npz")
    out["smplx_keys"] = {k: list(np.asarray(zs[k]).shape) for k in zs.files}
    for k in ("body_pose", "pose_body", "betas"):
        pass
    # try common pose keys at their fps
    for k in zs.files:
        a = np.asarray(zs[k])
        if a.ndim == 2 and a.shape[1] >= 20 and a.shape[0] > 10 and a.dtype.kind == "f":
            for fps in (30.0,):
                s = stats(a, 1/fps, f"smplx:{k}@{fps}fps")
                out[f"smplx_{k}"] = s
            break
except Exception as e:
    out["smplx_err"] = str(e)

# SONIC rollout trace: desired vs actual tracking error
try:
    import csv as _csv
    with open(f"{base}/traces/full_{TURN}.csv") as f:
        rd = _csv.DictReader(f)
        cols = rd.fieldnames
        rows = list(rd)
    out["trace_cols"] = cols[:30]
    out["trace_n"] = len(rows)
except Exception as e:
    out["trace_err"] = str(e)

print(json.dumps(out, indent=1))
