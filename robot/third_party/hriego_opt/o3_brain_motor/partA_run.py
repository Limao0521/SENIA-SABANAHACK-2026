#!/usr/bin/env python3
"""Part A runner: python partA_run.py <lean|v1|v2> -> out/A_<m>/<clip>.npz (+ QC json). M2 serial, native_qp 15 Hz, fast prefix, armed, 1 warm-up."""
import json, sys, time
import numpy as np
from brain_server import *
m = sys.argv[1]; out = Path(__file__).resolve().parent / "out" / f"A_{m}{sys.argv[2] if len(sys.argv)>2 else ''}"; out.mkdir(parents=True, exist_ok=True)
import librosa
b = Brain(models=m, armed=True); ld = b.load(); print("LOAD", ld, flush=True)
clips = json.load(open(CLIPS_JSON))
b.arm(); w, _ = b.run(librosa.load(clips["c010"], sr=16000)[0][:48000]); print("WARM", w["first_cmd_s"], flush=True)
rows = []
for name, wav in clips.items():
    a = librosa.load(wav, sr=16000)[0]; b.arm(); r, d = b.run(a, keep_q=True)
    qc = qc_for(d["gmr_q"], 15); r["qc"] = {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v)) for k, v in qc.items()}
    r["name"] = name; r.pop("windows"); rows.append(r); np.savez(out / f"{name}.npz", **d)
    print(name, round(r["first_cmd_s"], 3), "fell", r["fell"], "tilt", round(r["tilt_max"], 1), "pel", round(r["pelvis_min"], 3), "qc", qc.get("status"), flush=True); trim()
json.dump(dict(models=m, load=ld, rows=rows), open(out / "summary.json", "w"), indent=1, default=float)
print("DONE", flush=True); sys.stdout.flush(); os._exit(0)
