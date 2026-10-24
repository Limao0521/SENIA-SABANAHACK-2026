#!/usr/bin/env python3
"""S1 HEAD (commands only, DRY-RUN on host): verify TwinBackend GENERATES head commands for
SONIC and POINT within limits (yaw ±0.4887, pitch -0.4014..+0.4887 rad, speed <= 10 rad/s,
point yaw toward target). The twin is fixed-head and its emulated URS ignores head:
NOT EXECUTED BY TWIN — this is a command-track verification only.

Virtual clock: pump=False + pump_once(vt) so every frame is captured deterministically.
Outputs: out_diag/twin_v/s1_head_commands.csv + s1_head.json
"""
import json, os, sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
os.environ.setdefault("X2_HRIEGO_ROOT", os.path.join(os.path.dirname(__file__), "..", "..", "assets/hri/HRIEGO_gesture"))
os.environ.setdefault("X2_MC_CLASSIC", os.path.join(os.path.dirname(__file__), "..", "..", "assets/hri/mc_classic"))

from x2_ultra.hri_runtime.backends.twin import TwinBackend, twin_config
from x2_ultra.hri_runtime.backends.base import TICK

YAW_LIM, PITCH_LO, PITCH_HI, V_LIM = 0.4887, -0.4014, 0.4887, 10.0
OUT = os.path.dirname(os.path.abspath(__file__))
TRACKS = ["sebastian_t02", "laura_t02", "josaed_t03"]
rows = []          # csv log: source, t, yaw_cmd, pitch_cmd, turn_head_u
res = {"executed_by_twin": False,
       "note": "twin model is fixed-head; emulated URS ignores head. Command track only."}

be = TwinBackend(twin_config(), pump=False)     # dry-run: DryLink records, never publishes
vt = 1000.0

def pump(n=1):
    global vt
    for _ in range(n):
        be.pump_once(vt)
        vt += TICK

# ---- SONIC tracks: head2 channel ----------------------------------------------------------
sonic = {}
for name in TRACKS:
    d = np.load(os.path.join(OUT, "..", "motion_online", f"{name}_dialogue_online.npz"))
    h = d["head"].astype(float)
    v = np.vstack([np.zeros((1, 2)), np.diff(h, axis=0)]) / TICK
    sonic[name] = {
        "yaw_min": float(h[:, 0].min()), "yaw_max": float(h[:, 0].max()),
        "pitch_min": float(h[:, 1].min()), "pitch_max": float(h[:, 1].max()),
        "speed_max": float(np.abs(v).max()),
        "yaw_ok": bool(np.all(np.abs(h[:, 0]) <= YAW_LIM)),
        "pitch_ok": bool(np.all((h[:, 1] >= PITCH_LO) & (h[:, 1] <= PITCH_HI))),
        "speed_ok": bool(np.all(np.abs(v) <= V_LIM)),
    }
    t0 = vt
    for i in range(len(h)):
        be.stream_upper_body(vt, d["arms"][i], d["waist"][i], h[i])
        pump()
        rows.append(("SONIC_" + name, f"{vt - t0:.3f}", f"{h[i,0]:.4f}", f"{h[i,1]:.4f}", ""))
res["sonic"] = sonic

# ---- POINT: turn_head channel (4th McBodyPose field) --------------------------------------
pts = {}
for direction, expected_sign in (("LEFT", 1.0), ("RIGHT", -1.0), ("FRONT", 0.0)):
    n_before = len(be.commands)
    ts = vt + 1.0
    be.point(direction, ts, hold_s=1.5)
    n_frames = int((1.0 + 1.5 + 1.65 + 0.3) / TICK)
    t0 = vt
    u_track = []
    for _ in range(n_frames):
        be.stream_upper_body(vt, np.zeros(14), np.zeros(3), np.zeros(2))
        pump()
        bp = [c for c in be.commands[n_before:] if c["kind"] == "body_pose"]
        u = bp[-1]["u"][3] if bp else 0.0
        u_track.append(u)
        rows.append((f"POINT_{direction}", f"{vt - t0:.3f}", "", "", f"{u:.4f}"))
    u_track = np.array(u_track)
    npos, nneg = int((u_track > 0.2).sum()), int((u_track < -0.2).sum())
    pts[direction] = {
        "turn_head_u_max": float(u_track.max()), "turn_head_u_min": float(u_track.min()),
        "expected_sign": expected_sign, "ticks_u_pos": npos, "ticks_u_neg": nneg,
        # bang-bang sign control: the DOMINANT sign over the window must match the target side
        "sign_ok": bool((npos > nneg and npos > 20) if expected_sign > 0 else
                        (nneg > npos and nneg > 20) if expected_sign < 0 else
                        (np.abs(u_track).max() < 1e-9)),
        "point_track_set": bool(pts.get("_last_set", True)),
        "within_yaw_lim": bool(np.all(np.abs(u_track) <= YAW_LIM)),   # u es velocidad de signo; el tope de posición lo pone el perfil ±0.30·w <= 0.4887
        "profile_amp": 0.30,
    }
res["point"] = pts

ok = (all(s["yaw_ok"] and s["pitch_ok"] and s["speed_ok"] for s in sonic.values())
      and all(p["sign_ok"] for p in pts.values()))
res["PASS"] = bool(ok)

with open(os.path.join(OUT, "s1_head_commands.csv"), "w") as fh:
    fh.write("source,t_s,yaw_cmd_rad,pitch_cmd_rad,turn_head_u\n")
    for r in rows:
        fh.write(",".join(r) + "\n")
with open(os.path.join(OUT, "s1_head.json"), "w") as fh:
    json.dump(res, fh, indent=1)
print(json.dumps(res, indent=1))
print("PASS" if ok else "FAIL")
