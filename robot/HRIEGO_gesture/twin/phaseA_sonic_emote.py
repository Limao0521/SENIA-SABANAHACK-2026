"""c12 Phase A: SONIC base for the 20 emote turns of visits_v2 via the warm workers (S1-S4) + prep_arms (env laplace).
Output layout identical to sonic_batch.py: visits_v2/<rank>/sonic/<turn>/{wav16,smplx,x2,sonic,traces,logs,<turn>_arms.npz,status.json}. Skips turns with an existing arms.npz.
Run with the physicalai python:  phaseA_sonic_emote.py"""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import json, subprocess, sys, time
from pathlib import Path
import numpy as np, soundfile as sf
from scipy.signal import resample_poly
H = Path(__file__).resolve().parent; V = H.parent / "visits_v2"; W = H.parent / "workers"
sys.path.insert(0, str(W)); from wlib import Worker, mem_avail_gb
PREP = str(_LR / "experiments/RCP-01/runs/20261021_rcp01c07_ready_pose/scripts/prep_arms.py")
LOG = H / "runs/c12_transitions/logs"
turns = []
for rk in "vip regular guest staff external".split():
    for l in open(V / rk / "tts_manifest.jsonl"):
        d = json.loads(l)
        if d.get("motion_id") is not None: turns.append((rk, d))
print(len(turns), "emote turns; MemAvailable", round(mem_avail_gb(), 1), flush=True)
ws = {k: Worker(k, LOG) for k in ("st", "gmr", "sonic")}
print("workers ready; MemAvailable", round(mem_avail_gb(), 1), flush=True)
res = []
try:
    for rk, d in turns:
        name = d["turn_id"]; M = V / rk / "sonic" / name; arms = M / f"{name}_arms.npz"
        if arms.exists(): print("skip", name); continue
        t0 = time.monotonic(); (M / "wav16").mkdir(parents=True, exist_ok=True)
        x, sr = sf.read(d["wav_path"], dtype="float32"); assert sr == 44100
        w16 = M / f"wav16/{name}.wav"; sf.write(w16, resample_poly(x, 160, 441).astype(np.float32), 16000, subtype="PCM_16")
        ok = False; att = []
        for seed in (1, 2, 3, 4):
            r1 = ws["st"].call(id=name, wav=str(w16), out=str(M / f"smplx/{name}.npz"), seed=seed)
            if not r1["ok"]: att.append({"seed": seed, "st_fail": r1}); break
            r2 = ws["gmr"].call(id=name, smplx=str(M / f"smplx/{name}.npz"), name=name, out_dir=str(M / "x2/retarget"))
            att.append({"seed": seed, "gmr_status": r2.get("status"), "qc_fail": (r2.get("qc") or {}).get("fail")})
            if r2["ok"] and r2["status"] == "PASS": ok = True; break
            if "self_collision" not in str((r2.get("qc") or {}).get("fail")): break
        if ok:
            r3 = ws["sonic"].call(id=name, name=name, retarget_dir=str(M / "x2/retarget"), sonic_dir=str(M / "sonic"), log_dir=str(M / "logs"), trace_dir=str(M / "traces"))
            ok = bool(r3["ok"]) and (M / f"traces/full_{name}.csv").is_file()
            if ok:
                p = subprocess.run([_conda(), "run", "--no-capture-output", "-n", "laplace", "python", PREP, str(M / f"traces/full_{name}.csv"), str(arms)], capture_output=True, text=True)
                ok = p.returncode == 0 and arms.is_file()
        st = {"clip": name, "group": rk, "emote": d["emote"], "audio_s": d["duration_s"], "dir": str(M), "ok": ok, "attempts": att, "wall_s": round(time.monotonic() - t0, 1), "workers": "persistent (c12 phase A)"}
        (M / "status.json").write_text(json.dumps(st, indent=1)); res.append(st)
        print(name, ok, att, st["wall_s"], "MemAvail", round(mem_avail_gb(), 1), flush=True)
finally:
    for w in ws.values(): w.stop()
json.dump(res, open(H / "runs/c12_transitions/metrics/phaseA_status.json", "w"), indent=1)
