#!/usr/bin/env python3
"""Offline reference = the CURRENT production chain (GMR native_qp worker in env gmr -> SONIC convert+rollout worker -> prep_arms) applied to the
SAME StreamTalk output the online server produced (out/<tag>/<clip>.npz: st_poses/st_trans). Serial, one worker at a time. out/offline_ref/<clip>/arms.npz."""
import json, subprocess, sys, time
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parent.parent / "workers")); sys.path.insert(0, str(HERE.parent.parent))
import paths as P
from wlib import Worker
tag = sys.argv[1]; out = HERE / "out/offline_ref"; out.mkdir(exist_ok=True); (out / "logs").mkdir(exist_ok=True)
z = np.load(P.READY_PROMPT, allow_pickle=True); betas = z["betas"].astype(np.float32)
clips = [p.stem for p in sorted((HERE / "out" / tag).glob("*.npz"))]
for c in clips:
    d = np.load(HERE / "out" / tag / f"{c}.npz")
    (out / c).mkdir(exist_ok=True)
    np.savez(out / c / f"{c}_smplx.npz", betas=betas, poses=d["st_poses"], trans=d["st_trans"], gender=np.array("neutral"), mocap_frame_rate=np.array(30))
rows = {}
w = Worker("gmr", out / "logs"); print("gmr ready", w.ready.get("spawn_to_ready_s"), flush=True)
for c in clips:
    b = out / c; r = w.call(id=c, smplx=str(b / f"{c}_smplx.npz"), name=c, out_dir=str(b / "retarget")); rows[c] = {"gmr": {k: r.get(k) for k in ("status", "qc", "frames", "retarget_s", "wall_s", "ok")}}; print(c, r.get("status"), r.get("retarget_s"), flush=True)
w.stop(); time.sleep(2)
w = Worker("sonic", out / "logs"); print("sonic ready", flush=True)
for c in clips:
    b = out / c
    r = w.call(id=c, name=c, retarget_dir=str(b / "retarget"), sonic_dir=str(b / "sonic"), log_dir=str(b / "logs"), trace_dir=str(b / "traces"))
    o = subprocess.run([P.py_physicalai(), str(P.MOTION_DIR / "prep_arms.py"), r["trace"], str(b / "arms.npz")], capture_output=True, text=True)
    rows[c]["sonic"] = {k: r.get(k) for k in ("sim_s", "rollout_s", "ok")}; rows[c]["prep"] = json.loads(o.stdout) if o.returncode == 0 else o.stderr[-200:]; print(c, "sonic", r.get("ok"), flush=True)
w.stop(); json.dump(rows, open(out / "offline_ref.json", "w"), indent=1)
